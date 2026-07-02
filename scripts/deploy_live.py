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
        restarts also reload the live prerequisite sidecars before preflight,
        and still require scripts/check_live_restart_preflight.py to pass.

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
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.edli_queue import (
    EDLI_REACTOR_PROCESSING_LEASE_SECONDS,
    collect_edli_queue_evidence,
)
from src.ops.monitor_cadence import collect_monitor_cadence_evidence

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
LIVE_TRADING_PREREQUISITE_LABELS = tuple(
    DAEMONS[key]
    for key in (
        "data-ingest",
        "forecast-live",
        "substrate-observer",
        "price-channel-ingest",
        "post-trade-capital",
        "riskguard-live",
        "venue-heartbeat",
    )
)
LAUNCHD_BOOTSTRAP_ATTEMPTS = 6
LAUNCHD_BOOTSTRAP_RETRY_SECONDS = 2.0
LAUNCHD_UNLOAD_WAIT_SECONDS = 8.0
LAUNCHD_UNLOAD_POLL_SECONDS = 0.5
LIVE_RUNTIME_FRESH_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_RUNTIME_FRESH_VERIFY_TIMEOUT_SECONDS", "90")
)
LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS", "240")
)
LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS", "240")
)
LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS = 1.0
LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_RUNTIME_FRESH_CLOCK_TOLERANCE_SECONDS", "5")
)
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
    args = ("rev-parse", "--short", "HEAD") if short else ("rev-parse", "HEAD")
    res = _git(*args)
    return (res.stdout.strip().splitlines() or ["?"])[0] or "?"


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


def _live_trading_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    try:
        payload = plistlib.loads(LIVE_TRADING_PLIST.read_bytes())
        plist_env = payload.get("EnvironmentVariables")
        if isinstance(plist_env, dict):
            env.update({str(key): str(value) for key, value in plist_env.items()})
    except Exception:
        pass
    return env


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


def _wait_for_launchctl_unloaded(label: str) -> bool:
    deadline = time.monotonic() + LAUNCHD_UNLOAD_WAIT_SECONDS
    while time.monotonic() < deadline:
        if not _launchctl_service_loaded(label):
            return True
        time.sleep(LAUNCHD_UNLOAD_POLL_SECONDS)
    return not _launchctl_service_loaded(label)


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
        if not _wait_for_launchctl_unloaded(label):
            return False, f"FAILED reload stop {label}: service still loaded after bootout"

    last_boot: subprocess.CompletedProcess | None = None
    for attempt in range(1, LAUNCHD_BOOTSTRAP_ATTEMPTS + 1):
        boot = subprocess.run(
            ["launchctl", "bootstrap", GUI_DOMAIN, str(plist)],
            capture_output=True,
            text=True,
            timeout=20.0,
        )
        last_boot = boot
        if boot.returncode == 0:
            verb = "reloaded" if was_loaded else "bootstrapped"
            suffix = "" if attempt == 1 else f" after {attempt} attempts"
            return True, f"{verb} {label} from {plist}{suffix}"
        if attempt < LAUNCHD_BOOTSTRAP_ATTEMPTS:
            time.sleep(LAUNCHD_BOOTSTRAP_RETRY_SECONDS * attempt)
    assert last_boot is not None
    return (
        False,
        f"FAILED bootstrap {label} after {LAUNCHD_BOOTSTRAP_ATTEMPTS} attempts: "
        f"rc={last_boot.returncode} {last_boot.stderr.strip()}",
    )


def _parse_iso_utc(raw: object) -> datetime | None:
    try:
        text = str(raw or "").replace("Z", "+00:00")
        if not text:
            return None
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _wait_for_live_runtime_fresh(
    *,
    expected_sha: str,
    launched_after: datetime,
    timeout_seconds: float = LIVE_RUNTIME_FRESH_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait until the booted live daemon proves it loaded the expected HEAD.

    launchctl bootstrap returning 0 only proves launchd accepted the plist. The
    money path needs a process-level proof: src.main writes state/loaded_sha.json
    at boot, then deployment_freshness clears its mismatch state on the next tick.
    Without this wait, a restart command can report success while live submit is
    still blocked by the previous deployment_freshness_mismatch.
    """

    live_repo = Path(_require_live_repo())
    loaded_path = live_repo / "state" / "loaded_sha.json"
    freshness_path = live_repo / "state" / "deployment_freshness.json"
    expected = str(expected_sha or "").strip()
    launched_floor = launched_after.astimezone(timezone.utc)
    launched_floor_with_tolerance = launched_floor - timedelta(
        seconds=max(0.0, LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS)
    )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_detail = "not checked"

    while True:
        loaded_payload = _load_json(loaded_path)
        loaded = str(
            loaded_payload.get("loaded_sha")
            or loaded_payload.get("boot_sha")
            or loaded_payload.get("current_sha")
            or ""
        ).strip()
        loaded_at = _parse_iso_utc(loaded_payload.get("generated_at"))
        loaded_ok = bool(
            expected
            and loaded == expected
            and loaded_at is not None
            and loaded_at >= launched_floor_with_tolerance
        )

        freshness_payload = _load_json(freshness_path)
        if freshness_payload:
            freshness_status = str(freshness_payload.get("status") or "").strip()
            freshness_pause = freshness_payload.get("pause_reason")
            freshness_boot = str(freshness_payload.get("boot_sha") or "").strip()
            freshness_current = str(freshness_payload.get("current_sha") or "").strip()
            freshness_at = _parse_iso_utc(freshness_payload.get("detected_at"))
            freshness_ok = (
                freshness_status == "fresh"
                and freshness_pause in (None, "")
                and freshness_boot == expected
                and freshness_current == expected
                and freshness_at is not None
                and freshness_at >= launched_floor_with_tolerance
            )
        else:
            # No stale mismatch file exists; loaded_sha is the process-level proof.
            freshness_status = "absent"
            freshness_ok = True

        if loaded_ok and freshness_ok:
            return (
                True,
                "live runtime freshness verified: "
                f"loaded_sha={loaded[:9]} deployment_freshness={freshness_status}",
            )

        last_detail = (
            f"loaded_sha={loaded[:9] if loaded else '<missing>'} "
            f"loaded_at={loaded_at.isoformat() if loaded_at else '<missing>'} "
            f"expected={expected[:9]} "
            f"deployment_freshness={freshness_status}"
        )
        if time.monotonic() >= deadline:
            return False, "live runtime freshness did not verify after restart: " + last_detail
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


def _wait_for_post_start_monitor_cadence(
    *,
    launched_after: datetime,
    timeout_seconds: float = LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait until held-position monitoring proves it ran after this boot.

    Chain reconciliation can refresh ``position_current.updated_at`` without any
    exit/hold decision.  The post-start recovery proof is a fresh canonical
    ``MONITOR_REFRESHED`` event after the live-trading launch floor while open
    positions exist.
    """

    trade_db = Path(_require_live_repo()) / "state" / "zeus_trades.db"
    launched_floor = launched_after.astimezone(timezone.utc) - timedelta(
        seconds=max(0.0, LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS)
    )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_detail = "not checked"

    while True:
        try:
            conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "position_current" not in tables or "position_events" not in tables:
                conn.close()
                last_detail = "position_current or position_events table missing"
            else:
                cadence = collect_monitor_cadence_evidence(
                    conn,
                    now=datetime.now(timezone.utc),
                    min_occurred_at=launched_floor,
                    sample_limit=5,
                )
                conn.close()
                open_count = int(cadence["open_position_count"])
                if open_count == 0:
                    return True, "post-start monitor cadence skipped: no open positions"
                if cadence["future_monitor_event_count"]:
                    last_detail = (
                        f"open_positions={open_count} "
                        f"future_monitor_events={cadence['future_monitor_event_count']} "
                        f"sample={cadence['future_monitor_events']}"
                    )
                else:
                    stale_or_missing = list(cadence["stale_or_missing_positions"])
                    if not stale_or_missing:
                        return (
                            True,
                            "post-start monitor cadence verified: "
                            f"all_positions_refreshed={open_count}",
                        )
                    sample = ", ".join(
                        f"{item['position_id']} last_monitor_refreshed_at={item['last_monitor_refreshed_at']}"
                        for item in stale_or_missing[:5]
                    )
                    last_detail = (
                        f"open_positions={open_count} "
                        f"stale_or_missing_positions={cadence['stale_or_missing_position_count']} "
                        f"sample={sample or '<empty>'} "
                        f"launched_floor={launched_floor.isoformat()}"
                    )
        except Exception as exc:  # noqa: BLE001
            last_detail = f"monitor cadence read failed: {type(exc).__name__}: {exc}"

        if time.monotonic() >= deadline:
            return (
                False,
                "post-start monitor cadence did not verify after restart: " + last_detail,
            )
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


def _wait_for_post_start_edli_queue_progress(
    *,
    launched_after: datetime,
    timeout_seconds: float = LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait until the EDLI reactor proves it can move claimable queue work."""

    world_db = Path(_require_live_repo()) / "state" / "zeus-world.db"
    launched_floor = launched_after.astimezone(timezone.utc) - timedelta(
        seconds=max(0.0, LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS)
    )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_detail = "not checked"

    while True:
        now = datetime.now(timezone.utc)
        try:
            conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            tables = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "opportunity_event_processing" not in tables:
                conn.close()
                last_detail = "opportunity_event_processing table missing"
            else:
                queue = collect_edli_queue_evidence(
                    conn,
                    now=now,
                    launched_floor=launched_floor,
                    processing_lease_seconds=EDLI_REACTOR_PROCESSING_LEASE_SECONDS,
                )
                conn.close()
                pending_count = int(queue["pending_count"])
                processing_count = int(queue["processing_count"])
                claimable_pending_count = int(queue["claimable_pending_count"])
                stale_processing_count = int(queue["stale_processing_count"])
                progressed_count = int(queue["claim_or_terminal_after_launch_count"])
                claimable_work_count = int(queue["claimable_work_count"])
                oldest_stale_claimed_at = str(queue["oldest_stale_claimed_at"] or "")
                if claimable_work_count == 0:
                    if progressed_count > 0:
                        return (
                            True,
                            "post-start EDLI queue progress verified: "
                            f"processing={processing_count} progressed={progressed_count}",
                        )
                    return (
                        True,
                        "post-start EDLI queue progress skipped: no claimable reactor work",
                    )
                if stale_processing_count == 0 and progressed_count > 0:
                    return (
                        True,
                        "post-start EDLI queue progress verified: "
                        f"claimable_pending={claimable_pending_count} "
                        f"processing={processing_count} progressed={progressed_count}",
                    )
                last_detail = (
                    f"pending={pending_count} processing={processing_count} "
                    f"claimable_pending={claimable_pending_count} "
                    f"stale_processing={stale_processing_count} "
                    f"oldest_stale_claimed_at={oldest_stale_claimed_at or '<none>'} "
                    f"progressed_after_launch={progressed_count} "
                    f"launched_floor={launched_floor.isoformat()}"
                )
        except Exception as exc:  # noqa: BLE001
            last_detail = f"EDLI queue read failed: {type(exc).__name__}: {exc}"

        if time.monotonic() >= deadline:
            return (
                False,
                "post-start EDLI queue progress did not verify after restart: "
                + last_detail,
            )
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


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
            env=_live_trading_subprocess_env(),
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


def _run_restart_recovery_if_needed(labels: list[str]) -> tuple[bool, str]:
    """Run bounded restart recovery before the read-only live-trading preflight."""

    if LIVE_TRADING_LABEL not in labels:
        return True, "restart recovery not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    code = (
        "import json; "
        "from src.execution.command_recovery import reconcile_unresolved_commands; "
        "summary = reconcile_unresolved_commands(scope='restart_preflight'); "
        "print(json.dumps(summary, sort_keys=True, default=str)); "
        "raise SystemExit(1 if int(summary.get('errors') or 0) else 0)"
    )
    try:
        res = subprocess.run(
            [py, "-c", code],
            cwd=live_repo,
            env=_live_trading_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=120.0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"live restart recovery could not run: {exc}"
    output = (res.stdout or res.stderr or "").strip()
    tail = "\n".join(output.splitlines()[-40:]) if output else "<no output>"
    if res.returncode != 0:
        return False, f"live restart recovery failed rc={res.returncode}:\n{tail}"
    try:
        summary = json.loads(output.splitlines()[-1])
    except Exception:
        summary = {}
    return True, f"live restart recovery passed: {json.dumps(summary, sort_keys=True)}"


def _dedupe_labels(labels: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        deduped.append(label)
    return deduped


def _restart_labels_for_target(target: str) -> list[str] | None:
    """Expand an operator restart target into launchd labels.

    A live-trading restart is not process-local anymore: restart preflight
    requires sidecar heartbeat code identity to match the checkout that will run
    ``src.main``.  If only live-trading is reloaded after a code change, the
    preflight correctly blocks on stale sidecar SHAs and leaves the trading
    daemon stopped.  Make that dependency explicit in the deployment tool by
    refreshing live prerequisites before the read-only preflight.
    """

    if target == "all":
        labels = list(DAEMONS.values())
    elif target in DAEMONS:
        labels = [DAEMONS[target]]
    else:
        return None

    if target != "all" and LIVE_TRADING_LABEL in labels:
        labels = [*LIVE_TRADING_PREREQUISITE_LABELS, *labels]
    return _dedupe_labels(labels)


def cmd_restart(args: argparse.Namespace) -> int:
    target = args.daemon
    labels = _restart_labels_for_target(target)
    if labels is None:
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
    live_was_loaded_before = (
        _launchctl_service_loaded(LIVE_TRADING_LABEL)
        if includes_live_trading
        else False
    )

    non_live_labels = [label for label in labels if label != LIVE_TRADING_LABEL]
    for label in non_live_labels:
        ok, detail = _launch_or_restart_label(label)
        if ok:
            print(detail)
        else:
            rc_all = 1
            print(detail, file=sys.stderr)
    if rc_all != 0:
        if includes_live_trading and live_was_loaded_before:
            print(
                "live-trading was not stopped; fix prerequisite daemon restart blockers "
                "before reloading it",
                file=sys.stderr,
            )
        elif includes_live_trading:
            print(
                "live-trading left stopped because a prerequisite daemon failed to restart",
                file=sys.stderr,
            )
        return rc_all

    recovery_ok, recovery_detail = _run_restart_recovery_if_needed(labels)
    if not recovery_ok:
        print("REFUSING to restart — live restart recovery is not green:")
        print(recovery_detail)
        if includes_live_trading and live_was_loaded_before:
            print(
                "live-trading was not stopped; fix restart recovery blockers before reloading it.",
                file=sys.stderr,
            )
        elif includes_live_trading:
            print("live-trading left stopped; fix restart recovery blockers before starting it.", file=sys.stderr)
        return 1
    print(recovery_detail)

    preflight_ok, preflight_detail = _run_restart_preflight_if_needed(labels)
    if not preflight_ok:
        print("REFUSING to restart — live restart preflight is not green:")
        print(preflight_detail)
        if includes_live_trading and live_was_loaded_before:
            print(
                "live-trading was not stopped; fix preflight blockers before reloading it.",
                file=sys.stderr,
            )
        elif includes_live_trading:
            print("live-trading left stopped; fix preflight blockers before starting it.", file=sys.stderr)
        return 1
    print(preflight_detail)

    if includes_live_trading:
        expected_live_sha = head_sha(short=False)
        ok, detail = _stop_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
        else:
            print(detail, file=sys.stderr)
        if not ok:
            return 1
        launched_after = datetime.now(timezone.utc)
        ok, detail = _launch_or_restart_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
            runtime_ok, runtime_detail = _wait_for_live_runtime_fresh(
                expected_sha=expected_live_sha,
                launched_after=launched_after,
            )
            print(runtime_detail)
            if not runtime_ok:
                rc_all = 1
            else:
                queue_ok, queue_detail = _wait_for_post_start_edli_queue_progress(
                    launched_after=launched_after,
                )
                print(queue_detail)
                if not queue_ok:
                    rc_all = 1
                monitor_ok, monitor_detail = _wait_for_post_start_monitor_cadence(
                    launched_after=launched_after,
                )
                print(monitor_detail)
                if not monitor_ok:
                    rc_all = 1
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
