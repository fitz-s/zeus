#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-06-12; last_reused=2026-06-12
# Purpose: make live daemon restarts SAFE — refuse `launchctl kickstart` while the LIVE
#   checkout's runtime surface is uncommitted/unpushed, and require live restart preflight
#   before booting the trading daemon.
# Reuse: read-mostly (git status/rev-parse + launchctl list + preflight checks); the only
#   state change is kickstart after the gates pass.
# Last reused/audited: 2026-06-12
# Authority basis: operator big-direction 2026-06-12 ("大方向现在也只是添加几个文件现在做") +
#   incident: a `launchctl kickstart` booted a concurrent agent's mid-edit working tree
#   into live money.
"""deploy_live — make live daemon restarts safe (deploy/dev split).

The Zeus daemon launchd plists define the checkout that live code boots from.
Restarting a daemon boots whatever is on disk there — so a kickstart while
that tree has uncommitted or unpushed runtime code ships half-finished work
into live money. This tool gates the restart against that same checkout.

COMMANDS
    deploy_live.py status
        Print HEAD sha, the live branch, whether HEAD is pushed, the dirty
        runtime files (src/ config/), and each daemon's pid + uptime.

    deploy_live.py restart <daemon|all>
        Start one daemon (short label, e.g. "live-trading") or all of
        them. REFUSES when the live checkout's src/ or config/ has
        uncommitted changes, OR when HEAD != origin/<branch> (unpushed) —
        printing exactly what is dirty / unpushed. Pass --allow-dirty to
        bypass only the git-surface gate with a loud warning. live-trading
        restarts still require scripts/check_live_restart_preflight.py to pass.

SAFETY
    Read-mostly: the only state-changing action is `launchctl bootout` followed
    by `launchctl bootstrap` from the active plist. A plain kickstart is not
    enough for this tool because it can preserve launchd's already-loaded
    EnvironmentVariables after a plist config fix. Reload happens only after the
    clean-tree gate passes (or --allow-dirty is given) and the live-money restart
    preflight passes for trading-daemon restarts.
    `status` never changes anything.

    USAGE
    .venv/bin/python scripts/deploy_live.py status
    .venv/bin/python scripts/deploy_live.py restart live-trading
    .venv/bin/python scripts/deploy_live.py restart all --allow-dirty
"""
from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LIVE_TRADING_PLIST = (
    Path.home() / "Library" / "LaunchAgents" / "com.zeus.live-trading.plist"
)
LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _resolve_live_repo() -> str:
    """Return the checkout that launchd will execute for live trading."""

    explicit = os.environ.get("ZEUS_LIVE_REPO")
    if explicit:
        return str(Path(explicit).expanduser().resolve())
    try:
        payload = plistlib.loads(LIVE_TRADING_PLIST.read_bytes())
    except Exception as exc:
        raise RuntimeError(
            f"cannot resolve live checkout: unreadable live-trading plist {LIVE_TRADING_PLIST}"
        ) from exc
    working_dir = payload.get("WorkingDirectory")
    if isinstance(working_dir, str) and working_dir.strip():
        return str(Path(working_dir).expanduser().resolve())
    raise RuntimeError(
        f"cannot resolve live checkout: {LIVE_TRADING_PLIST} has no WorkingDirectory"
    )


def _resolve_initial_live_repo() -> str:
    try:
        return _resolve_live_repo()
    except RuntimeError:
        return ""


def _require_live_repo() -> str:
    if LIVE_REPO:
        return LIVE_REPO
    raise RuntimeError(
        "live checkout is unresolved; set ZEUS_LIVE_REPO or fix the live-trading plist"
    )


# The LIVE checkout the daemon boots from. Tests may still monkeypatch this.
LIVE_REPO = _resolve_initial_live_repo()

# launchd GUI domain for the operator user (gui/<uid>); ZEUS_GUI_DOMAIN overrides.
GUI_DOMAIN = os.environ.get("ZEUS_GUI_DOMAIN") or f"gui/{os.getuid()}"

# Short label -> full launchd label. "all" expands to every entry here.
DAEMONS = {
    "data-ingest": "com.zeus.data-ingest",
    "forecast-live": "com.zeus.forecast-live",
    "substrate-observer": "com.zeus.substrate-observer",
    "price-channel-ingest": "com.zeus.price-channel-ingest",
    "post-trade-capital": "com.zeus.post-trade-capital",
    "riskguard-live": "com.zeus.riskguard-live",
    "live-trading": "com.zeus.live-trading",
    "venue-heartbeat": "com.zeus.venue-heartbeat",
    "heartbeat-sensor": "com.zeus.heartbeat-sensor",
}
LIVE_TRADING_LABEL = "com.zeus.live-trading"

# Runtime surface whose dirtiness must block a restart (per the incident).
# scripts/ is included because daemon plists and operator flows execute
# scripts/*.py from the live checkout (external review 2026-06-12). deploy/launchd
# is included because the sidecar split is launchd-topology-sensitive; a clean
# code tree with stale plist artifacts is not a deploy-clean runtime. docs/ and
# tests/ are deliberately outside the gate.
RUNTIME_PATHSPECS = ["src/", "config/", "scripts/", "deploy/launchd/"]


def _git(*args: str, repo: str | None = None) -> subprocess.CompletedProcess:
    # Read LIVE_REPO at call time (not as a default-arg binding) so tests and
    # callers that point the gate at a different checkout are honored.
    checkout = repo or _require_live_repo()
    return subprocess.run(
        ["git", "-C", checkout, *args],
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
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip().splitlines()
        msg = detail[-1] if detail else "unknown git status failure"
        return [f"GIT_STATUS_FAILED: {msg}"]
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def unpushed_state(branch: str) -> tuple[bool, str]:
    """(is_unpushed, detail). True when HEAD != origin/<branch> or no upstream.

    Fail-closed freshness: fetches origin/<branch> first so the comparison is
    against the REMOTE's current state, not a stale local remote-tracking ref
    (external review 2026-06-12 — a stale origin/<branch> made the gate approve
    a checkout that was behind the actual remote). A failed fetch blocks.
    """
    local = _git("rev-parse", "HEAD").stdout.strip()
    fetch_res = _git("fetch", "--quiet", "origin", branch)
    if fetch_res.returncode != 0:
        detail = (fetch_res.stderr or fetch_res.stdout).strip().splitlines()
        return True, f"fetch origin/{branch} failed (fail-closed): {detail[-1] if detail else 'unknown'}"
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
    """(pid, status) for a launchd label, or ('-', '-') if not loaded.

    Fail-soft when launchctl is unavailable (non-macOS, e.g. Linux CI).
    """
    try:
        res = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=8.0
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return "-", "-"
    for ln in res.stdout.splitlines():
        if label in ln:
            parts = ln.split("\t") if "\t" in ln else ln.split()
            if len(parts) >= 3:
                return parts[0], parts[1]
    return "-", "-"


def _plist_path_for_label(label: str) -> Path:
    if label == "com.zeus.live-trading":
        return LIVE_TRADING_PLIST
    return LAUNCHAGENTS_DIR / f"{label}.plist"


def _launchctl_service_loaded(label: str) -> bool:
    try:
        res = subprocess.run(
            ["launchctl", "print", f"{GUI_DOMAIN}/{label}"],
            capture_output=True,
            text=True,
            timeout=8.0,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


def _launch_or_restart_label(label: str) -> tuple[bool, str]:
    plist = _plist_path_for_label(label)
    if not plist.exists():
        return False, f"FAILED bootstrap {label}: active plist missing at {plist}"

    was_loaded = _launchctl_service_loaded(label)
    if was_loaded:
        stop = subprocess.run(
            ["launchctl", "bootout", f"{GUI_DOMAIN}/{label}"],
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        if stop.returncode != 0:
            return False, f"FAILED reload stop {label}: rc={stop.returncode} {stop.stderr.strip()}"

    boot = subprocess.run(
        ["launchctl", "bootstrap", GUI_DOMAIN, str(plist)],
        capture_output=True,
        text=True,
        timeout=20.0,
    )
    if boot.returncode == 0:
        verb = "reloaded" if was_loaded else "bootstrapped"
        return True, f"{verb} {label} from {plist}"
    return False, f"FAILED bootstrap {label}: rc={boot.returncode} {boot.stderr.strip()}"


def _stop_label(label: str) -> tuple[bool, str]:
    """Stop/unload a launchd label so preflight can inspect an absent process."""

    if not _launchctl_service_loaded(label):
        return True, f"{label} already stopped"
    stop = subprocess.run(
        ["launchctl", "bootout", f"{GUI_DOMAIN}/{label}"],
        capture_output=True,
        text=True,
        timeout=20.0,
    )
    if stop.returncode == 0:
        return True, f"stopped {label}"
    return False, f"FAILED stop {label}: rc={stop.returncode} {stop.stderr.strip()}"


def cmd_status(_args: argparse.Namespace) -> int:
    try:
        _require_live_repo()
    except RuntimeError as exc:
        print(f"REFUSING status — {exc}", file=sys.stderr)
        return 2
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
    try:
        _require_live_repo()
    except RuntimeError as exc:
        return False, [str(exc)]
    branch = current_branch()
    blockers: list[str] = []
    dirty = dirty_runtime_files()
    if dirty:
        if any(line.startswith("GIT_STATUS_FAILED:") for line in dirty):
            blockers.append("git status failed for runtime surface (fail-closed):")
        else:
            blockers.append(f"{len(dirty)} uncommitted runtime file(s) in src/ config/ scripts/ deploy/launchd/:")
        blockers.extend(f"   {ln}" for ln in dirty)
    unpushed, push_detail = unpushed_state(branch)
    if unpushed:
        blockers.append(f"unpushed: {push_detail}")
    if blockers and not allow_dirty:
        return False, blockers
    return True, blockers


def _run_restart_preflight_if_needed(labels: list[str]) -> tuple[bool, str]:
    """Run the live-money preflight before booting the trading daemon.

    ``--allow-dirty`` is only a git-surface override. It must not bypass current
    DB/artifact/sidecar/held-position safety checks.
    """

    if LIVE_TRADING_LABEL not in labels:
        return True, "preflight not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    cmd = [py, "scripts/check_live_restart_preflight.py", "--json"]
    try:
        res = subprocess.run(
            cmd,
            cwd=live_repo,
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"live restart preflight could not run: {exc}"
    output = (res.stdout or res.stderr or "").strip()
    if res.returncode == 0:
        return True, "live restart preflight passed"
    tail = "\n".join(output.splitlines()[-80:]) if output else "<no output>"
    return False, f"live restart preflight failed rc={res.returncode}:\n{tail}"


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
    includes_live_trading = LIVE_TRADING_LABEL in labels
    live_was_stopped = False
    if includes_live_trading:
        ok, detail = _stop_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
        else:
            print(detail, file=sys.stderr)
        if not ok:
            return 1
        live_was_stopped = True

    non_live_labels = [label for label in labels if label != LIVE_TRADING_LABEL]
    for label in non_live_labels:
        ok, detail = _launch_or_restart_label(label)
        if ok:
            print(detail)
        else:
            rc_all = 1
            print(detail, file=sys.stderr)
    if rc_all != 0:
        if live_was_stopped:
            print(
                "live-trading left stopped because a prerequisite daemon failed to restart",
                file=sys.stderr,
            )
        return rc_all

    preflight_ok, preflight_detail = _run_restart_preflight_if_needed(labels)
    if not preflight_ok:
        print("REFUSING to restart — live restart preflight is not green:")
        print(preflight_detail)
        if live_was_stopped:
            print("live-trading left stopped; fix preflight blockers before starting it.", file=sys.stderr)
        return 1
    print(preflight_detail)

    if includes_live_trading:
        ok, detail = _launch_or_restart_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
        else:
            rc_all = 1
            print(detail, file=sys.stderr)
    return rc_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make live daemon restarts safe (deploy/dev split).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="show HEAD/dirty/push state + daemon pids")
    p_status.set_defaults(func=cmd_status)

    p_restart = sub.add_parser("restart", help="bootstrap or kickstart a daemon (gated on clean live tree)")
    p_restart.add_argument("daemon", help="short daemon label or 'all'")
    p_restart.add_argument("--allow-dirty", action="store_true",
                           help="bypass the clean-tree gate (loud warning)")
    p_restart.set_defaults(func=cmd_restart)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
