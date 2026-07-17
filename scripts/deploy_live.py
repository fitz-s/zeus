#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: make live daemon restarts SAFE — refuse `launchctl kickstart` while the LIVE
#   checkout's runtime surface is uncommitted/unpushed, and require live restart preflight
#   before booting the trading daemon.
# Reuse: read-mostly (git status/rev-parse + launchctl list + preflight checks); the only
#   state change is kickstart after the gates pass.
# Last reused/audited: 2026-07-16
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
        printing exactly what is dirty / unpushed. Pass --allow-unpushed to
        bypass only the pushed-state gate for an otherwise clean tree, or
        --allow-dirty to bypass the full git-surface gate with a loud warning.
        live-trading restarts also reload the live prerequisite sidecars before
        preflight, and still require scripts/check_live_restart_preflight.py to pass.

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
    .venv/bin/python scripts/deploy_live.py restart live-trading --allow-unpushed
    .venv/bin/python scripts/deploy_live.py restart all --allow-dirty
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
import textwrap
import time
from contextlib import contextmanager, suppress
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
from src.control.runtime_code_plane import is_runtime_code_path

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
LIVE_RESTART_LOCK_FILENAME = "deploy-live-restart.lock"
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
LIVE_PREREQUISITE_READY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_PREREQUISITE_READY_TIMEOUT_SECONDS", "90")
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
PREREQUISITE_CODE_HEARTBEATS = {
    DAEMONS["forecast-live"]: ("forecast-live-heartbeat.json", ("written_at", "timestamp")),
    DAEMONS["substrate-observer"]: ("daemon-heartbeat-substrate-observer.json", ("alive_at",)),
    DAEMONS["price-channel-ingest"]: ("daemon-heartbeat-price-channel-ingest.json", ("alive_at",)),
    DAEMONS["post-trade-capital"]: ("daemon-heartbeat-post-trade-capital.json", ("alive_at",)),
}
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
    lines: list[str] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        raw_path = line[3:].strip().replace("\\", "/")
        candidates = (
            [part.strip() for part in raw_path.split(" -> ", 1)]
            if " -> " in raw_path
            else [raw_path]
        )
        if any(is_runtime_code_path(candidate) for candidate in candidates):
            lines.append(line)
    return lines


def unpushed_state(branch: str) -> tuple[bool, str]:
    """(is_unpushed, detail). True when HEAD != origin/<branch> or no upstream.

    Fail-closed freshness: fetches origin/<branch> first so the comparison is
    against the REMOTE's current state, not a stale local remote-tracking ref
    (external review 2026-06-12 — a stale origin/<branch> made the gate approve
    a checkout that was behind the actual remote). A failed fetch blocks.
    """
    local = _git("rev-parse", "HEAD").stdout.strip()
    try:
        fetch_res = _git("fetch", "--quiet", "origin", branch)
    except subprocess.TimeoutExpired:
        return True, f"fetch origin/{branch} timed out (fail-closed)"
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


def _git_head_matches(expected: str, observed: str) -> bool:
    """Match a full HEAD to the >=7-hex abbreviation emitted by sidecars."""

    expected = str(expected or "").strip()
    observed = str(observed or "").strip()
    return bool(
        expected
        and observed
        and (
            expected == observed
            or (len(observed) >= 7 and expected.startswith(observed))
        )
    )


def _wait_for_prerequisite_code_identity(
    labels: list[str],
    *,
    expected_sha: str,
    launched_after: datetime,
    timeout_seconds: float = LIVE_PREREQUISITE_READY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait for restarted sidecars to prove the code identity preflight checks."""

    targets = [label for label in labels if label in PREREQUISITE_CODE_HEARTBEATS]
    if not targets:
        return True, "sidecar code identity wait not required"
    state_dir = Path(_require_live_repo()) / "state"
    expected = str(expected_sha or "").strip()
    floor = launched_after.astimezone(timezone.utc) - timedelta(
        seconds=max(0.0, LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS)
    )
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_pending: list[str] = []

    while True:
        pending: list[str] = []
        for label in targets:
            filename, time_keys = PREREQUISITE_CODE_HEARTBEATS[label]
            payload = _load_json(state_dir / filename)
            observed = str(payload.get("git_head") or "").strip()
            observed_at = next(
                (
                    parsed
                    for key in time_keys
                    if (parsed := _parse_iso_utc(payload.get(key))) is not None
                ),
                None,
            )
            if (
                not _git_head_matches(expected, observed)
                or observed_at is None
                or observed_at < floor
            ):
                pending.append(
                    f"{label}:sha={observed[:9] if observed else '<missing>'} "
                    f"at={observed_at.isoformat() if observed_at else '<missing>'}"
                )
        if not pending:
            return True, f"sidecar code identity verified for {len(targets)} prerequisite(s)"
        last_pending = pending
        if time.monotonic() >= deadline:
            return False, "sidecar code identity did not verify after restart: " + "; ".join(last_pending)
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


def _wait_for_live_runtime_fresh(
    *,
    expected_sha: str,
    launched_after: datetime,
    timeout_seconds: float = LIVE_RUNTIME_FRESH_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait until the booted live daemon proves it loaded the expected HEAD.

    launchctl bootstrap returning 0 only proves launchd accepted the plist. The
    money path needs a process-level proof: src.main writes state/loaded_sha.json
    at boot. ``deployment_freshness.json`` compares that immutable process
    identity with a mutable checkout, so a concurrent improvement commit is an
    operator observation, not evidence that the already-loaded process is stale
    or unauthorized. Requiring both identities to remain equal makes a healthy
    restart impossible while the repository's improvement loop is active.
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
        else:
            freshness_status = "absent"

        if loaded_ok:
            return (
                True,
                "live process identity verified: "
                f"loaded_sha={loaded[:9]} "
                f"worktree_freshness_observation={freshness_status}",
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

    state_dir = Path(_require_live_repo()) / "state"
    world_db = state_dir / "zeus-world.db"
    trade_db = state_dir / "zeus_trades.db"
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
                auction_receipt = _latest_complete_global_auction_receipt(
                    trade_db,
                    launched_floor=launched_floor,
                )
                if stale_processing_count == 0 and auction_receipt is not None:
                    receipt_id, candidate_count, scope_count = auction_receipt
                    return (
                        True,
                        "post-start EDLI queue progress verified: "
                        f"auction_receipt={receipt_id} candidates={candidate_count} "
                        f"scope_families={scope_count} "
                        f"claimable_pending={claimable_pending_count}",
                    )
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


def _latest_complete_global_auction_receipt(
    trade_db: Path,
    *,
    launched_floor: datetime,
) -> tuple[int, int, int] | None:
    """Return a post-launch complete auction as direct reactor progress proof."""

    if not trade_db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, started_at, completed_at, artifact_json
              FROM decision_log
             WHERE mode = 'global_single_order_auction'
             ORDER BY id DESC
             LIMIT 8
            """
        ).fetchall()
        conn.close()
    except Exception:
        return None
    for row in rows:
        try:
            artifact = json.loads(row["artifact_json"] or "{}")
            summary = artifact.get("summary") or {}
            completed_at = _parse_iso_utc(
                artifact.get("completed_at") or row["completed_at"] or row["started_at"]
            )
            candidate_count = int(summary.get("candidate_evaluation_count") or 0)
            scope_count = int(summary.get("full_scope_family_count") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            completed_at is not None
            and completed_at >= launched_floor
            and summary.get("candidate_coverage_complete") is True
            and summary.get("scope_family_coverage_complete") is True
            and candidate_count > 0
            and scope_count > 0
        ):
            return int(row["id"]), candidate_count, scope_count
    return None


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


def _runtime_status_summary() -> dict:
    """Read the live status projection without mutating runtime state."""

    live_repo = Path(_require_live_repo())
    payload = _load_json(live_repo / "state" / "status_summary.json")
    if not payload:
        return {"present": False}
    return {
        "present": True,
        "generated_at": payload.get("generated_at") or payload.get("timestamp"),
        "status": payload.get("status"),
        "mode": payload.get("mode"),
        "live_action_authorized": payload.get("live_action_authorized"),
        "failure_reason": payload.get("failure_reason"),
        "live_boot": payload.get("live_boot"),
        "execution_capability": payload.get("execution_capability"),
    }


def _status_payload() -> tuple[int, dict]:
    try:
        _require_live_repo()
    except RuntimeError as exc:
        return 2, {
            "ok": False,
            "issue": "LIVE_REPO_UNRESOLVED",
            "detail": str(exc),
        }
    branch = current_branch()
    unpushed, push_detail = unpushed_state(branch)
    dirty = dirty_runtime_files()
    daemons = {}
    for short, label in DAEMONS.items():
        pid, status = daemon_pid_uptime(label)
        daemons[short] = {
            "label": label,
            "pid": None if pid == "-" else pid,
            "last_status": None if status == "-" else status,
            "loaded": pid != "-" or status != "-",
        }
    gate_ok, gate_blockers = _gate(allow_dirty=False, allow_unpushed=False)
    return 0, {
        "ok": True,
        "live_repo": LIVE_REPO,
        "branch": branch,
        "head": head_sha(),
        "push_state": {
            "unpushed": unpushed,
            "detail": push_detail,
        },
        "dirty_runtime_files": dirty,
        "restart_gate": {
            "ok": gate_ok,
            "blockers": gate_blockers,
        },
        "runtime_status": _runtime_status_summary(),
        "daemons": daemons,
    }


def cmd_status(args: argparse.Namespace) -> int:
    rc, payload = _status_payload()
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True, default=str))
        return rc
    if rc != 0:
        print(f"REFUSING status — {payload.get('detail')}", file=sys.stderr)
        return rc
    branch = str(payload["branch"])
    push_state = payload["push_state"]
    dirty = list(payload["dirty_runtime_files"])
    print(f"deploy_live status  (live checkout: {LIVE_REPO})")
    print("=" * 64)
    print(f"branch     : {branch}")
    print(f"HEAD       : {payload['head']}")
    print(
        "push state : "
        f"{'UNPUSHED — ' if push_state['unpushed'] else 'clean — '}"
        f"{push_state['detail']}"
    )
    if dirty:
        print(f"dirty runtime surface ({len(dirty)} entries):")
        for ln in dirty:
            print(f"   {ln}")
    else:
        print("dirty runtime surface : (clean)")
    restart_gate = payload["restart_gate"]
    if not restart_gate["ok"]:
        print(f"restart gate: BLOCKED ({len(restart_gate['blockers'])} blockers)")
    else:
        print("restart gate: ok")
    runtime_status = payload.get("runtime_status") or {}
    if runtime_status.get("present"):
        status = runtime_status.get("status") or "?"
        mode = runtime_status.get("mode") or "?"
        live_authorized = runtime_status.get("live_action_authorized")
        print(
            "runtime status: "
            f"{status} mode={mode} live_action_authorized={live_authorized}"
        )
        live_boot = runtime_status.get("live_boot") or {}
        live_boot_issue = live_boot.get("issue") if isinstance(live_boot, dict) else None
        if live_boot_issue:
            print(f"runtime boot : {live_boot_issue}")
        failure_reason = runtime_status.get("failure_reason")
        if failure_reason:
            print(f"runtime failure: {failure_reason}")
    else:
        print("runtime status: (no status_summary.json)")
    print("daemons:")
    for short, row in payload["daemons"].items():
        pid = row["pid"] if row["pid"] is not None else "-"
        status = row["last_status"] if row["last_status"] is not None else "-"
        print(f"   {short:<16} pid={pid:<8} last-status={status}")
    return 0


def _gate(allow_dirty: bool, allow_unpushed: bool = False) -> tuple[bool, list[str]]:
    """Return (ok_to_restart, blockers). ok=False means refuse."""
    try:
        _require_live_repo()
    except RuntimeError as exc:
        return False, [str(exc)]
    branch = current_branch()
    blockers: list[str] = []
    dirty_blockers: list[str] = []
    unpushed_blockers: list[str] = []
    dirty = dirty_runtime_files()
    if dirty:
        if any(line.startswith("GIT_STATUS_FAILED:") for line in dirty):
            dirty_blockers.append("git status failed for runtime surface (fail-closed):")
        else:
            dirty_blockers.append(f"{len(dirty)} uncommitted runtime file(s) in src/ config/ scripts/ deploy/launchd/:")
        dirty_blockers.extend(f"   {ln}" for ln in dirty)
    unpushed, push_detail = unpushed_state(branch)
    if unpushed:
        unpushed_blockers.append(f"unpushed: {push_detail}")
    if dirty_blockers and not allow_dirty:
        blockers.extend(dirty_blockers)
    if unpushed_blockers and not (allow_dirty or allow_unpushed):
        blockers.extend(unpushed_blockers)
    if blockers:
        return False, blockers
    blockers.extend(dirty_blockers)
    blockers.extend(unpushed_blockers)
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
    env = _live_trading_subprocess_env()
    env["ZEUS_LIVE_RESTART_IN_PROGRESS"] = "1"
    try:
        res = subprocess.run(
            cmd,
            cwd=live_repo,
            env=env,
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


def _ensure_restart_world_schemas(conn: sqlite3.Connection) -> None:
    """Atomically materialize world schemas required by the deployed HEAD."""

    from src.state.schema.edli_live_order_events_schema import (
        ensure_tables as ensure_live_order_tables,
    )
    from src.state.schema.edli_live_profit_audit_schema import (
        ensure_table as ensure_live_profit_audit_table,
    )
    from src.state.schema.settlement_attribution_schema import (
        ensure_table as ensure_settlement_attribution_table,
    )

    conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_live_order_tables(conn)
        ensure_live_profit_audit_table(conn)
        ensure_settlement_attribution_table(conn)
    except Exception:
        conn.rollback()
        raise
    conn.commit()


def _run_restart_recovery_if_needed(labels: list[str]) -> tuple[bool, str]:
    """Run bounded restart recovery before the read-only live-trading preflight."""

    if LIVE_TRADING_LABEL not in labels:
        return True, "restart recovery not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    code = textwrap.dedent(
        """
        import json
        from scripts.migrations import apply_migrations
        from scripts.deploy_live import _ensure_restart_world_schemas
        from src.state.db import (
            get_trade_connection,
            get_world_connection,
            get_world_connection_with_trades_required,
            init_schema_trade_only,
        )
        applied = {}

        world_conn = get_world_connection(write_class='live')
        try:
            _ensure_restart_world_schemas(world_conn)
            applied['world'] = apply_migrations(
                world_conn,
                target='202607_drop_world_collateral_unsettled_ghost',
                db_identity='world',
            )
            world_conn.commit()
        finally:
            world_conn.close()

        trade_conn = get_trade_connection(write_class='live')
        try:
            init_schema_trade_only(trade_conn)
            applied['trade'] = apply_migrations(
                trade_conn,
                target='202607_cas_reservation_ledger',
                db_identity='trade',
            )
            init_schema_trade_only(trade_conn)
            trade_conn.commit()
        finally:
            trade_conn.close()

        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.events.edli_trade_fact_bridge import (
            append_confirmed_trade_facts_to_edli,
            append_rest_filled_orphan_trade_facts_to_edli,
        )

        summary = reconcile_unresolved_commands(scope='restart_preflight')
        bridge_conn = get_world_connection_with_trades_required(write_class='live')
        try:
            summary['confirmed_fill_bridge_appended'] = append_confirmed_trade_facts_to_edli(
                bridge_conn
            )
            summary['rest_fill_orphan_bridge_appended'] = (
                append_rest_filled_orphan_trade_facts_to_edli(bridge_conn)
            )
            bridge_conn.commit()
        finally:
            bridge_conn.close()
        summary['schema_migrations_applied'] = applied
        print(json.dumps(summary, sort_keys=True, default=str))
        raise SystemExit(1 if int(summary.get('errors') or 0) else 0)
        """
    ).strip()
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


def _pause_entries_for_live_restart_if_needed(labels: list[str]) -> tuple[bool, str]:
    """Durably pause entries before a live-trading restart can boot new code.

    Restarting ``src.main`` creates a short window where deployment-freshness
    mismatch clears before an operator can manually re-apply an entry pause.
    Write the DB control override first so the new daemon starts in observe /
    monitor-only posture. ``pause_entries`` preserves an existing indefinite
    operator pause instead of overwriting it.
    """

    if LIVE_TRADING_LABEL not in labels:
        return True, "entry pause not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    code = textwrap.dedent(
        """
        from datetime import datetime, timezone
        from src.control.control_plane import pause_entries
        from src.state.db import get_world_connection

        now = datetime.now(timezone.utc).isoformat()
        conn = get_world_connection()
        try:
            row = conn.execute(
                '''
                SELECT reason, issued_by, issued_at
                  FROM control_overrides
                 WHERE target_type = 'global'
                   AND target_key = 'entries'
                   AND action_type = 'gate'
                   AND lower(COALESCE(value, '')) IN ('1', 'true', 'yes', 'on')
                   AND issued_by IN ('control_plane', 'operator')
                   AND effective_until IS NULL
                   AND issued_at <= ?
                 ORDER BY precedence DESC, issued_at DESC, override_id DESC
                 LIMIT 1
                ''',
                (now,),
            ).fetchone()
        finally:
            conn.close()

        if row is not None:
            print(
                'entries pause guard preserved: '
                f"issued_by={row[1]} reason={row[0]}"
            )
        else:
            pause_entries('deploy_live_restart_guard', issued_by='control_plane', effective_until=None)
            print('entries pause guard armed')
        """
    ).strip()
    try:
        res = subprocess.run(
            [py, "-c", code],
            cwd=live_repo,
            env=_live_trading_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"live restart entry pause guard could not run: {exc}"
    output = (res.stdout or res.stderr or "").strip()
    tail = "\n".join(output.splitlines()[-20:]) if output else "<no output>"
    if res.returncode != 0:
        return False, f"live restart entry pause guard failed rc={res.returncode}:\n{tail}"
    return True, f"live restart entry pause guard armed: {tail}"


def _resume_entries_after_verified_live_restart_if_needed(
    labels: list[str],
) -> tuple[bool, str]:
    """Clear only this deploy's guard after every post-start proof is green."""

    if LIVE_TRADING_LABEL not in labels:
        return True, "entry resume not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    code = textwrap.dedent(
        """
        from datetime import datetime, timezone
        from src.control.control_plane import resume_entries
        from src.state.db import get_world_connection

        now = datetime.now(timezone.utc).isoformat()
        conn = get_world_connection()
        try:
            row = conn.execute(
                '''
                SELECT reason, issued_by
                  FROM control_overrides
                 WHERE target_type = 'global'
                   AND target_key = 'entries'
                   AND action_type = 'gate'
                   AND lower(COALESCE(value, '')) IN ('1', 'true', 'yes', 'on')
                   AND issued_by IN ('control_plane', 'operator')
                   AND (effective_until IS NULL OR effective_until > ?)
                   AND issued_at <= ?
                 ORDER BY precedence DESC, issued_at DESC, override_id DESC
                 LIMIT 1
                ''',
                (now, now),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            print('entries pause guard already clear')
        elif row[0] == 'deploy_live_restart_guard' and row[1] == 'control_plane':
            resume_entries(
                'deploy_live_restart_guard_verified_runtime_queue_monitor',
                issued_by='control_plane',
            )
            print('deploy live restart guard cleared')
        else:
            print(
                'entries pause guard preserved after deploy: '
                f"issued_by={row[1]} reason={row[0]}"
            )
        """
    ).strip()
    try:
        res = subprocess.run(
            [py, "-c", code],
            cwd=live_repo,
            env=_live_trading_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"verified live restart entry resume could not run: {exc}"
    output = (res.stdout or res.stderr or "").strip()
    tail = "\n".join(output.splitlines()[-20:]) if output else "<no output>"
    if res.returncode != 0:
        return False, f"verified live restart entry resume failed rc={res.returncode}:\n{tail}"
    return True, f"verified live restart entry posture: {tail}"


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


class LiveRestartLockError(RuntimeError):
    """Raised when deploy cannot establish exclusive restart ownership."""


def _live_restart_lock_path() -> Path:
    return (
        Path(_require_live_repo())
        / "state"
        / "locks"
        / LIVE_RESTART_LOCK_FILENAME
    )


@contextmanager
def _live_restart_exclusive_lock():
    """Serialize deploy with the heartbeat watchdog bootstrap critical section."""

    path = _live_restart_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError as exc:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
        raise LiveRestartLockError(
            f"cannot acquire live restart lock {path}: {exc}"
        ) from exc
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield path
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def cmd_restart(args: argparse.Namespace) -> int:
    labels = _restart_labels_for_target(args.daemon)
    if labels is None or LIVE_TRADING_LABEL not in labels:
        return _cmd_restart_locked(args)
    try:
        with _live_restart_exclusive_lock():
            return _cmd_restart_locked(args)
    except LiveRestartLockError as exc:
        print(f"REFUSING to restart — {exc}", file=sys.stderr)
        return 1


def _cmd_restart_locked(args: argparse.Namespace) -> int:
    target = args.daemon
    labels = _restart_labels_for_target(target)
    if labels is None:
        print(f"unknown daemon '{target}'. known: {', '.join(DAEMONS)}, or 'all'", file=sys.stderr)
        return 2

    ok, blockers = _gate(args.allow_dirty, args.allow_unpushed)
    if not ok:
        print("REFUSING to restart — live runtime surface is not deploy-clean:")
        for b in blockers:
            print(f"  {b}")
        print("\nCommit runtime changes, push HEAD, pass --allow-unpushed for a clean local HEAD, or pass --allow-dirty to override.")
        return 1
    if blockers and args.allow_dirty:
        print("!" * 64)
        print("WARNING --allow-dirty: restarting with a DIRTY / UNPUSHED live tree.")
        print("This boots uncommitted runtime code into LIVE money. Blockers:")
        for b in blockers:
            print(f"  {b}")
        print("!" * 64)
    elif blockers and args.allow_unpushed:
        print("!" * 64)
        print("WARNING --allow-unpushed: restarting a clean but unpushed live HEAD.")
        print("This permits local committed runtime code into LIVE money. Blockers:")
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

    pause_ok, pause_detail = _pause_entries_for_live_restart_if_needed(labels)
    if not pause_ok:
        print("REFUSING to restart — live entry pause guard is not armed:")
        print(pause_detail)
        return 1
    print(pause_detail)

    expected_live_sha = ""
    launched_after: datetime | None = None
    non_live_labels = [label for label in labels if label != LIVE_TRADING_LABEL]
    # The venue-heartbeat service owns the heartbeat supervisor, which repairs an
    # absent live-trading service.  Keep it unloaded through the process-absent
    # preflight or it will correctly—but prematurely—bootstrap live-trading.
    post_live_labels = (
        [DAEMONS["venue-heartbeat"]]
        if includes_live_trading and DAEMONS["venue-heartbeat"] in non_live_labels
        else []
    )
    preflight_prerequisite_labels = [
        label for label in non_live_labels if label not in post_live_labels
    ]
    if includes_live_trading:
        expected_live_sha = head_sha(short=False)
        ok, detail = _stop_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
        else:
            print(detail, file=sys.stderr)
            return 1
        for label in non_live_labels:
            ok, detail = _stop_label(label)
            if ok:
                print(detail)
            else:
                print(detail, file=sys.stderr)
                return 1

    recovery_ok, recovery_detail = _run_restart_recovery_if_needed(labels)
    if not recovery_ok:
        print("REFUSING to restart — live restart recovery is not green:")
        print(recovery_detail)
        if includes_live_trading:
            print("live-trading left stopped; fix restart recovery blockers before starting it.", file=sys.stderr)
        return 1
    print(recovery_detail)

    prerequisite_launch_started_at = datetime.now(timezone.utc)
    for label in preflight_prerequisite_labels:
        ok, detail = _launch_or_restart_label(label)
        if ok:
            print(detail)
        else:
            rc_all = 1
            print(detail, file=sys.stderr)
    if rc_all != 0:
        if includes_live_trading:
            print(
                "live-trading left stopped because a prerequisite daemon failed to restart",
                file=sys.stderr,
            )
        return rc_all

    if includes_live_trading:
        prerequisite_ok, prerequisite_detail = _wait_for_prerequisite_code_identity(
            preflight_prerequisite_labels,
            expected_sha=expected_live_sha,
            launched_after=prerequisite_launch_started_at,
        )
        if not prerequisite_ok:
            print("REFUSING to restart — live prerequisite code identity is not ready:")
            print(prerequisite_detail)
            print(
                "live-trading left stopped; fix prerequisite daemon startup before starting it.",
                file=sys.stderr,
            )
            return 1
        print(prerequisite_detail)

    preflight_ok, preflight_detail = _run_restart_preflight_if_needed(labels)
    if not preflight_ok:
        print("REFUSING to restart — live restart preflight is not green:")
        print(preflight_detail)
        if includes_live_trading:
            print("live-trading left stopped; fix preflight blockers before starting it.", file=sys.stderr)
        return 1
    print(preflight_detail)

    if includes_live_trading:
        launched_after = datetime.now(timezone.utc)
        ok, detail = _launch_or_restart_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
            runtime_ok, runtime_detail = _wait_for_live_runtime_fresh(
                expected_sha=expected_live_sha,
                launched_after=launched_after,
            )
            print(runtime_detail)
            queue_ok = False
            monitor_ok = False
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
            # The supervisor must not observe the new daemon until its first
            # queue and monitor writes have replaced stale PID-bearing status.
            # Otherwise it correctly repairs the stale PID by restarting the
            # process that deploy_live is still trying to verify.
            post_live_ok = True
            for label in post_live_labels:
                deferred_ok, deferred_detail = _launch_or_restart_label(label)
                if deferred_ok:
                    print(deferred_detail)
                else:
                    post_live_ok = False
                    rc_all = 1
                    print(deferred_detail, file=sys.stderr)
            if runtime_ok and queue_ok and monitor_ok and post_live_ok:
                resume_ok, resume_detail = (
                    _resume_entries_after_verified_live_restart_if_needed(labels)
                )
                print(resume_detail)
                if not resume_ok:
                    rc_all = 1
        else:
            rc_all = 1
            print(detail, file=sys.stderr)
    return rc_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Make live daemon restarts safe (deploy/dev split).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="show HEAD/dirty/push state + daemon pids")
    p_status.add_argument("--json", action="store_true", help="emit machine-readable status JSON")
    p_status.set_defaults(func=cmd_status)

    p_restart = sub.add_parser("restart", help="bootstrap or kickstart a daemon (gated on clean live tree)")
    p_restart.add_argument("daemon", help="short daemon label or 'all'")
    p_restart.add_argument("--allow-unpushed", action="store_true",
                           help="allow clean committed HEAD that is not at origin/<branch>; dirty runtime files still block")
    p_restart.add_argument("--allow-dirty", action="store_true",
                           help="bypass the clean-tree gate (loud warning)")
    p_restart.set_defaults(func=cmd_restart)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
