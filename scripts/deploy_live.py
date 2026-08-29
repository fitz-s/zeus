#!/usr/bin/env python3
# Lifecycle: created=2026-06-12; last_reviewed=2026-08-29; last_reused=2026-08-29
# Purpose: make live daemon restarts SAFE — refuse `launchctl kickstart` while the LIVE
#   checkout's runtime surface is uncommitted/unpushed, and require live restart preflight
#   before booting the trading daemon.
# Reuse: read-mostly (git status/rev-parse + launchctl list + preflight checks); the only
#   state change is kickstart after the gates pass.
# Last reused/audited: 2026-08-29
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
import math
import os
import plistlib
import sqlite3
import stat
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
from src.ops.monitor_cadence import (
    collect_monitor_cadence_evidence,
    monitor_restart_blocking_evidence,
)
from src.state.db import query_control_override_state
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
RESTART_WORLD_MIGRATION_TARGETS = (
    ("world", "202607_drop_world_collateral_unsettled_ghost"),
    ("world_active_redecision_projection", "202608_edli_active_redecision_projection"),
    (
        "world_active_redecision_backfill_notnull",
        "202608_edli_active_redecision_projection_receipt_notnull",
    ),
)
RESTART_TRADE_MIGRATION_TARGETS = (
    ("trade", "202607_cas_reservation_ledger"),
)
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
LIVE_PREREQUISITE_REUSE_MAX_AGE_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_PREREQUISITE_REUSE_MAX_AGE_SECONDS", "90")
)
LIVE_PRESTOP_HANDOFF_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_PRESTOP_HANDOFF_VERIFY_TIMEOUT_SECONDS", "90")
)
# The runtime deliberately spreads a full held book over as many as three
# two-minute monitor cycles.  Four minutes cannot prove that six-minute
# contract; allow one additional cycle for launch jitter and DB observation.
LIVE_MONITOR_FULL_COVERAGE_CYCLES = 3
LIVE_MONITOR_CADENCE_CONTRACT_SECONDS = (
    LIVE_MONITOR_FULL_COVERAGE_CYCLES * 2 * 60
)
# This is deliberately longer than the held-quote sidecar freshness below: it
# identifies a monitor that is truly stalled, rather than a normal in-flight
# monitor pass.  Only that total-stall shape may recover by stopping it.
LIVE_STUCK_MONITOR_RECOVERY_STALE_SECONDS = LIVE_MONITOR_CADENCE_CONTRACT_SECONDS
LIVE_HELD_QUOTE_SIDECAR_MAX_AGE_SECONDS = 180.0
FRESH_FAILED_MONITOR_NO_ACTION_ISSUES = frozenset(
    {
        # A current MONITOR_REFRESHED attempt explicitly found neither
        # probability nor held-side CLOB authority. It is not actionable, and
        # cannot stand in for a current price/probability handoff.
        "monitor_probability_and_clob_stale",
    }
)
LIVE_MONITOR_CADENCE_VERIFY_GRACE_SECONDS = 2 * 60
LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get(
        "ZEUS_DEPLOY_LIVE_MONITOR_CADENCE_VERIFY_TIMEOUT_SECONDS",
        str(
            LIVE_MONITOR_CADENCE_CONTRACT_SECONDS
            + LIVE_MONITOR_CADENCE_VERIFY_GRACE_SECONDS
        ),
    )
)
LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS", "240")
)
LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS = 1.0
LIVE_RUNTIME_FRESH_VERIFY_CLOCK_TOLERANCE_SECONDS = float(
    os.environ.get("ZEUS_DEPLOY_LIVE_RUNTIME_FRESH_CLOCK_TOLERANCE_SECONDS", "5")
)
PREREQUISITE_CODE_HEARTBEATS = {
    DAEMONS["data-ingest"]: ("daemon-heartbeat-ingest.json", ("alive_at",)),
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


def _current_prerequisite_code_identity_labels(
    labels: list[str],
    *,
    expected_sha: str,
    now: datetime | None = None,
) -> set[str]:
    """Return loaded sidecars that already prove the requested code identity.

    SCOPE is one heartbeat-governed prerequisite label. DRAIN retains that
    healthy process instead of flushing its quote/forecast cache during a
    retry. RESET is any unload, SHA mismatch, invalid/future timestamp, or age
    beyond ``LIVE_PREREQUISITE_REUSE_MAX_AGE_SECONDS``; that label then follows
    the ordinary bootout/bootstrap path.
    """

    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    state_dir = Path(_require_live_repo()) / "state"
    expected = str(expected_sha or "").strip()
    reusable: set[str] = set()
    for label in labels:
        heartbeat = PREREQUISITE_CODE_HEARTBEATS.get(label)
        if heartbeat is None or not _launchctl_service_loaded(label):
            continue
        filename, time_keys = heartbeat
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
        if not _git_head_matches(expected, observed) or observed_at is None:
            continue
        age_seconds = (observed_now - observed_at).total_seconds()
        if 0.0 <= age_seconds <= LIVE_PREREQUISITE_REUSE_MAX_AGE_SECONDS:
            reusable.add(label)
    return reusable


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
    """Wait until every held position has a current attempt after this boot.

    Chain reconciliation can refresh ``position_current.updated_at`` without any
    exit/hold decision.  The deployment gate and the restart-guard reset must use
    the same full-book proof; accepting one coverage tranche here would leave the
    global entry pause selected when the reset immediately checks every position.

    SCOPE: this deploy invocation's entry pause, across canonical open exposure.
    DRAIN: recurring held monitoring emits fresh ``MONITOR_REFRESHED`` evidence
    for every open position within the configured three-cycle contract. A typed
    post-boot probability-degraded attempt is restart coverage when its held-side
    CLOB is fresh; runtime family admission remains blocked until probability
    authority recovers. RESET: zero restart-blocking/future evidence after the
    launch floor; any newly opened, unevaluated, or CLOB-blind position restores
    the wait.
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
                    monitor_refreshed_only=True,
                    require_fresh_inputs=True,
                    sample_limit=5,
                )
                conn.close()
                non_monitor_chain_risk_count = int(
                    cadence.get("non_monitor_chain_risk_position_count") or 0
                )
                open_count = int(cadence["open_position_count"])
                if non_monitor_chain_risk_count:
                    last_detail = (
                        f"open_positions={open_count} "
                        "non_monitor_chain_risk_position_count="
                        f"{non_monitor_chain_risk_count} "
                        f"sample={cadence.get('non_monitor_chain_risk_positions', [])}"
                    )
                elif open_count == 0:
                    return True, "post-start monitor cadence skipped: no open positions"
                elif cadence["future_monitor_event_count"]:
                    last_detail = (
                        f"open_positions={open_count} "
                        f"future_monitor_events={cadence['future_monitor_event_count']} "
                        f"sample={cadence['future_monitor_events']}"
                    )
                else:
                    fresh_count = int(cadence["fresh_position_count"])
                    cadence_groups = monitor_restart_blocking_evidence(cadence)
                    blocking_count = int(
                        cadence_groups["restart_blocking_stale_position_count"]
                    )
                    probability_degraded_count = int(
                        cadence_groups["probability_only_stale_position_count"]
                    )
                    quote_only_count = int(
                        cadence_groups["quote_only_stale_position_count"]
                    )
                    stale_or_missing = list(
                        cadence_groups["restart_blocking_stale_positions"]
                    )
                    held_position_ids = tuple(
                        str(value or "").strip()
                        for value in cadence.get("monitored_position_ids", ())
                    )
                    identity_complete = (
                        len(held_position_ids) == open_count
                        and all(held_position_ids)
                        and len(set(held_position_ids)) == len(held_position_ids)
                    )
                    auction_receipt = (
                        _latest_complete_global_auction_receipt(
                            trade_db,
                            launched_floor=launched_floor,
                            require_held_coverage_count=open_count,
                            require_held_position_ids=held_position_ids,
                        )
                        if identity_complete
                        and blocking_count == 0
                        and quote_only_count > 0
                        else None
                    )
                    if identity_complete and blocking_count == 0 and (
                        quote_only_count == 0 or auction_receipt is not None
                    ):
                        auction_detail = ""
                        if auction_receipt is not None:
                            receipt_id, candidate_count, scope_count = auction_receipt
                            auction_detail = (
                                f" quote_only_positions={quote_only_count} "
                                f"held_auction_receipt={receipt_id} "
                                f"candidates={candidate_count} "
                                f"scope_families={scope_count}"
                            )
                        return (
                            True,
                            "post-start monitor cadence verified: "
                            f"fresh_positions={fresh_count} "
                            f"open_positions={open_count} full_book=true"
                            f" probability_degraded_positions={probability_degraded_count}"
                            f"{auction_detail}",
                        )
                    if blocking_count == 0 and quote_only_count > 0:
                        last_detail = (
                            f"open_positions={open_count} "
                            f"fresh_positions={fresh_count} "
                            f"quote_only_positions={quote_only_count} "
                            "complete_post_start_held_auction_receipt=missing "
                            f"launched_floor={launched_floor.isoformat()}"
                        )
                        if time.monotonic() >= deadline:
                            return (
                                False,
                                "post-start monitor cadence did not verify after restart: "
                                + last_detail,
                            )
                        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)
                        continue
                    sample = ", ".join(
                        f"{item['position_id']} last_monitor_refreshed_at={item['last_monitor_refreshed_at']}"
                        for item in stale_or_missing[:5]
                    )
                    last_detail = (
                        f"open_positions={open_count} "
                        f"stale_or_missing_positions={blocking_count} "
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
    post_start_freshness_verified: bool,
    timeout_seconds: float = LIVE_EDLI_QUEUE_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait for EDLI queue progress, or prove paused entry work is intentionally parked.

    SCOPE: only an EDLI entry backlog while the durable global entries gate is
    active. DRAIN: resume entries, or let the reactor claim/terminalize work.
    RESET: a canonical held exposure, non-terminal SELL command, held-SELL
    global-auction debt, stale claim, unreadable pause state, or missing
    post-start freshness proof immediately restores the ordinary progress gate.
    """

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
                if claimable_work_count > 0:
                    parked_ok, parked_detail = _paused_entry_backlog_is_expected_parked(
                        world_db=world_db,
                        trade_db=trade_db,
                        state_dir=state_dir,
                        queue=queue,
                        post_start_freshness_verified=post_start_freshness_verified,
                    )
                    if parked_ok:
                        return True, parked_detail
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
                if claimable_work_count > 0:
                    last_detail += f" expected_parked={parked_detail}"
        except Exception as exc:  # noqa: BLE001
            last_detail = f"EDLI queue read failed: {type(exc).__name__}: {exc}"

        if time.monotonic() >= deadline:
            return (
                False,
                "post-start EDLI queue progress did not verify after restart: "
                + last_detail,
            )
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


def _paused_entry_backlog_is_expected_parked(
    *,
    world_db: Path,
    trade_db: Path,
    state_dir: Path,
    queue: dict[str, object],
    post_start_freshness_verified: bool,
) -> tuple[bool, str]:
    """Return whether a claimable EDLI entry backlog is safe to leave parked.

    This is deliberately an acceptance exception, not a queue mutation or an
    event-type filter. Every authority input is canonical: the WORLD durable
    pause projection, TRADE position/command projections, and the durable
    held-SELL wake queue. Any unreadable required surface raises so the caller
    remains fail-closed rather than treating it as empty debt.
    """

    stale_processing_count = int(queue.get("stale_processing_count") or 0)
    if stale_processing_count:
        return False, f"stale_processing={stale_processing_count}"
    if not post_start_freshness_verified:
        return False, "post_start_freshness=unverified"

    pause = _durable_entries_pause_state(world_db)
    pause_status = str(pause.get("status") or "unknown")
    if pause_status != "ok":
        raise RuntimeError(f"EDLI_EXPECTED_PARKED_PAUSE_UNREADABLE:{pause_status}")
    if not bool(pause.get("entries_paused")):
        return False, "durable_entries_paused=false"

    unresolved_position_count = _canonical_unresolved_position_count(trade_db)
    if unresolved_position_count:
        return False, f"canonical_unresolved_positions={unresolved_position_count}"

    nonterminal_sell_count = _nonterminal_sell_command_count(trade_db)
    if nonterminal_sell_count:
        return False, f"nonterminal_sell_commands={nonterminal_sell_count}"

    held_sell_debt_count = _held_sell_global_auction_debt_count(state_dir)
    if held_sell_debt_count:
        return False, f"held_sell_global_auction_debt={held_sell_debt_count}"

    return (
        True,
        "post-start EDLI queue expected parked: "
        "durable_entries_paused=true monitor_restart_coverage=verified "
        "canonical_unresolved_positions=0 "
        "nonterminal_sell_commands=0 held_sell_global_auction_debt=0 "
        "stale_processing=0 post_start_monitor_coverage=verified "
        f"claimable_pending={int(queue.get('claimable_pending_count') or 0)}",
    )


def _canonical_unresolved_position_count(trade_db: Path) -> int:
    """Count malformed or unresolved canonical positions, not healthy exposure."""

    from src.contracts.position_truth import (
        CURRENT_MONEY_RISK_CHAIN_STATES,
        NO_CURRENT_MONEY_RISK_CHAIN_STATES,
    )

    if not trade_db.exists():
        raise RuntimeError("EDLI_EXPECTED_PARKED_TRADE_DB_MISSING")
    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        columns = _sqlite_table_columns(conn, "position_current")
        required = {"phase", "chain_state", "shares", "chain_shares"}
        if not required.issubset(columns):
            raise RuntimeError("EDLI_EXPECTED_PARKED_POSITION_PROJECTION_UNREADABLE")
        governed_phases = (
            "pending_entry",
            "active",
            "day0_window",
            "pending_exit",
            "economically_closed",
            "settled",
            "voided",
            "admin_closed",
        )
        rows = conn.execute(
            """
            SELECT phase, chain_state, shares, chain_shares
              FROM position_current
            """
        ).fetchall()

        def finite_nonnegative(value: object) -> float | None:
            try:
                parsed = float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
            if parsed is None or not math.isfinite(parsed) or parsed < 0.0:
                return None
            return parsed

        open_phases = {"active", "day0_window", "pending_exit"}
        no_risk_states = set(NO_CURRENT_MONEY_RISK_CHAIN_STATES)
        current_risk_states = set(CURRENT_MONEY_RISK_CHAIN_STATES)
        unresolved = 0
        for phase_raw, chain_state_raw, shares_raw, chain_shares_raw in rows:
            phase = str(phase_raw or "").strip().lower()
            chain_state = str(chain_state_raw or "").strip().lower()
            shares = finite_nonnegative(shares_raw)
            chain_shares = finite_nonnegative(chain_shares_raw)
            shares_invalid = shares_raw is not None and shares is None
            chain_shares_invalid = chain_shares_raw is not None and chain_shares is None
            positive_exposure = any(
                value is not None and value > 0.000001
                for value in (shares, chain_shares)
            )

            if phase not in governed_phases:
                unresolved += 1
                continue
            if phase in open_phases:
                # Open exposure is allowed only when both local and chain
                # quantities are finite/positive and the chain still owns
                # money risk.  A zero-share open phase is not a healthy held
                # position and cannot satisfy restart monitor proof.
                if (
                    shares is None
                    or chain_shares is None
                    or shares <= 0.000001
                    or chain_shares <= 0.000001
                ):
                    unresolved += 1
                elif chain_state not in current_risk_states:
                    unresolved += 1
                continue
            if phase == "pending_entry":
                # An entry projection must still be empty and explicitly
                # classified as carrying no current chain money risk.  NULL
                # quantities are the canonical unfilled projection shape and
                # therefore mean no proved exposure here, not malformed risk.
                if (
                    shares_invalid
                    or chain_shares_invalid
                    or positive_exposure
                    or chain_state not in no_risk_states
                ):
                    unresolved += 1
                continue

            # Settled/economically-closed/history rows legitimately retain
            # their last shares and chain snapshot; those are attribution
            # facts, not monitor obligations.  A voided position is the one
            # terminal phase that can still contradict venue truth, so only
            # positive current-risk chain exposure there blocks recovery.
            if (
                phase == "voided"
                and chain_state in current_risk_states
                and (
                    chain_shares_invalid
                    or (chain_shares is not None and chain_shares > 0.000001)
                )
            ):
                unresolved += 1
        return unresolved
    except sqlite3.Error as exc:
        raise RuntimeError("EDLI_EXPECTED_PARKED_POSITION_PROJECTION_UNREADABLE") from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass


def _canonical_live_restart_obligations(trade_db: Path) -> dict[str, object]:
    """Return capital obligations that make a loaded-daemon restart unsafe.

    A healthy open position is not an error, but it still needs uninterrupted
    probability and exit monitoring. Likewise, a non-terminal venue command
    can fill while the process is absent. This is deliberately stricter than
    ``_canonical_unresolved_position_count``.
    """

    from src.execution.command_bus import TERMINAL_STATES

    if not trade_db.exists():
        raise RuntimeError("LIVE_RESTART_TRADE_DB_MISSING")
    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        position_columns = _sqlite_table_columns(conn, "position_current")
        if not {"position_id", "phase"}.issubset(position_columns):
            raise RuntimeError("LIVE_RESTART_POSITION_PROJECTION_UNREADABLE")
        command_columns = _sqlite_table_columns(conn, "venue_commands")
        if not {"command_id", "state"}.issubset(command_columns):
            raise RuntimeError("LIVE_RESTART_COMMAND_PROJECTION_UNREADABLE")

        open_phases = ("pending_entry", "active", "day0_window", "pending_exit")
        phase_placeholders = ", ".join("?" for _ in open_phases)
        position_ids = tuple(
            str(row[0])
            for row in conn.execute(
                f"""
                SELECT position_id
                  FROM position_current
                 WHERE LOWER(COALESCE(phase, '')) IN ({phase_placeholders})
                 ORDER BY position_id
                """,
                open_phases,
            ).fetchall()
        )

        terminal_states = sorted(
            {state.value for state in TERMINAL_STATES} | {"CANCELED", "FAILED"}
        )
        terminal_placeholders = ", ".join("?" for _ in terminal_states)
        command_ids = tuple(
            str(row[0])
            for row in conn.execute(
                f"""
                SELECT command_id
                  FROM venue_commands
                 WHERE UPPER(COALESCE(state, '')) NOT IN ({terminal_placeholders})
                 ORDER BY command_id
                """,
                tuple(terminal_states),
            ).fetchall()
        )
        return {
            "open_position_count": len(position_ids),
            "open_position_ids": position_ids[:10],
            # The display sample remains bounded, but the exceptional recovery
            # admission must prove classification against every open position.
            "all_open_position_ids": position_ids,
            "nonterminal_command_count": len(command_ids),
            "nonterminal_command_ids": command_ids[:10],
        }
    except sqlite3.Error as exc:
        raise RuntimeError("LIVE_RESTART_CANONICAL_OBLIGATIONS_UNREADABLE") from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass


def _pre_stop_monitor_handoff_evidence(trade_db: Path) -> dict[str, object]:
    """Prove the loaded daemon just covered every held position and quote.

    Probability-only degradation is admissible because this handoff exists to
    deploy a probability repair.  Missing or stale held-side CLOB evidence is
    never admissible: the replacement process must inherit a recently observed
    executable state, not a blind portfolio.
    """

    now = datetime.now(timezone.utc)
    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        cadence = collect_monitor_cadence_evidence(
            conn,
            now=now,
            max_age_seconds=LIVE_MONITOR_CADENCE_CONTRACT_SECONDS,
            monitor_refreshed_only=True,
            require_fresh_inputs=True,
            sample_limit=256,
        )
        groups = monitor_restart_blocking_evidence(cadence)
        event_handoff_ids = _exact_v4_reauction_restart_handoff_ids(
            conn,
            positions=groups.get("restart_blocking_stale_positions", ()),
            now=now,
        )
        lineage_handoff_ids = _v4_lineage_reauction_restart_handoff_ids(
            conn,
            positions=groups.get("restart_blocking_stale_positions", ()),
        )
        reauction_handoff_ids = tuple(
            sorted({*event_handoff_ids, *lineage_handoff_ids})
        )
    except (RuntimeError, sqlite3.Error) as exc:
        return {
            "green": False,
            "reason": f"pre_stop_monitor_evidence_unreadable:{type(exc).__name__}",
        }
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass

    open_count = int(cadence.get("open_position_count") or 0)
    monitored_ids = tuple(
        str(value or "").strip()
        for value in cadence.get("monitored_position_ids", ())
    )
    probability_degraded_count = int(
        groups.get("probability_only_stale_position_count") or 0
    )
    restart_blocking_count = int(
        groups.get("restart_blocking_stale_position_count") or 0
    )
    reauction_handoff_count = len(reauction_handoff_ids)
    restart_blocking_count -= reauction_handoff_count
    quote_only_count_raw = groups.get("quote_only_stale_position_count")
    quote_only_shape_error: str | None = None
    if isinstance(quote_only_count_raw, bool) or not isinstance(
        quote_only_count_raw, int
    ) or quote_only_count_raw < 0:
        quote_only_count = 0
        quote_only_shape_error = "count_invalid"
    else:
        quote_only_count = quote_only_count_raw
    fresh_count = int(cadence.get("fresh_position_count") or 0)
    probability_positions = list(
        groups.get("probability_only_stale_positions") or []
    )
    quote_only_raw = groups.get("quote_only_stale_positions")
    if quote_only_raw is None:
        quote_only_positions: list[object] = []
    elif isinstance(quote_only_raw, list):
        quote_only_positions = quote_only_raw
    else:
        quote_only_positions = []
        quote_only_shape_error = quote_only_shape_error or "records_not_list"
    quote_only_ids_list: list[str] = []
    if quote_only_shape_error is None and len(quote_only_positions) != quote_only_count:
        quote_only_shape_error = "count_mismatch"
    if quote_only_shape_error is None:
        for item in quote_only_positions:
            if not isinstance(item, dict):
                quote_only_shape_error = "record_not_mapping"
                break
            position_id = str(item.get("position_id") or "").strip()
            if not position_id:
                quote_only_shape_error = "position_id_missing"
                break
            if item.get("issue") != "monitor_clob_stale":
                quote_only_shape_error = "issue_invalid"
                break
            if position_id in quote_only_ids_list:
                quote_only_shape_error = "position_id_duplicate"
                break
            quote_only_ids_list.append(position_id)
    restart_blocking_positions = [
        item
        for item in list(groups.get("restart_blocking_stale_positions") or [])
        if isinstance(item, dict)
        and str(item.get("position_id") or "").strip()
        not in set(reauction_handoff_ids)
    ]
    fresh_failed_restart_positions = [
        item
        for item in restart_blocking_positions
        if str(item.get("issue") or "") in FRESH_FAILED_MONITOR_NO_ACTION_ISSUES
    ]
    # ``collect_monitor_cadence_evidence`` gives this category only to the
    # canonical closed-market hold shape. It remains restart-only evidence;
    # settlement/exit authority is unchanged.
    fresh_failed_settlement_positions = [
        item
        for item in list(cadence.get("settlement_recoverable_positions") or [])
        if isinstance(item, dict)
        and item.get("cadence_source")
        in {
            "MONITOR_REFRESHED_CLOSED_MARKET_PENDING_SETTLEMENT",
            "PARTIAL_EXIT_REMAINDER_TERMINAL_RELEASED",
        }
    ]
    fresh_failed_positions = [
        *fresh_failed_restart_positions,
        *fresh_failed_settlement_positions,
    ]
    settlement_recoverable_ids = tuple(
        str(item.get("position_id") or "").strip()
        for item in fresh_failed_settlement_positions
        if str(item.get("position_id") or "").strip()
    )
    probability_ids = tuple(
        str(item.get("position_id") or "").strip()
        for item in probability_positions
        if isinstance(item, dict) and str(item.get("position_id") or "").strip()
    )
    restart_blocking_ids = tuple(
        str(item.get("position_id") or "").strip()
        for item in restart_blocking_positions
    )
    quote_only_ids = tuple(quote_only_ids_list)
    fresh_failed_ids = tuple(
        str(item.get("position_id") or "").strip()
        for item in fresh_failed_positions
        if str(item.get("position_id") or "").strip()
    )
    fresh_failed_id_counts = {
        position_id: fresh_failed_ids.count(position_id)
        for position_id in set(fresh_failed_ids)
    }
    fresh_failed_duplicate_ids = tuple(
        sorted(
            position_id
            for position_id, count in fresh_failed_id_counts.items()
            if count > 1
        )
    )
    fresh_failed_id_set = set(fresh_failed_ids)
    fresh_failed_other_ids = tuple(
        [
            *probability_ids,
            *quote_only_ids,
            *(
                position_id
                for position_id in restart_blocking_ids
                if position_id not in fresh_failed_id_set
            ),
            *reauction_handoff_ids,
        ]
    )
    classified_positions = [
        *probability_positions,
        *restart_blocking_positions,
        *quote_only_positions,
    ]
    stale_classified_ids: list[str] = []
    missing_monitor_timestamp_ids: list[str] = []
    invalid_monitor_timestamp_ids: list[str] = []
    stale_fresh_failed_timestamp_ids: list[str] = []
    for item in classified_positions:
        if not isinstance(item, dict):
            continue
        position_id = str(item.get("position_id") or "").strip()
        if not position_id:
            continue
        raw_occurred_at = item.get("last_monitor_refreshed_at")
        timestamp_text = str(raw_occurred_at or "").strip()
        if not timestamp_text:
            missing_monitor_timestamp_ids.append(position_id)
            continue
        try:
            parsed = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            invalid_monitor_timestamp_ids.append(position_id)
            continue
        if parsed.tzinfo is None:
            invalid_monitor_timestamp_ids.append(position_id)
            continue
        occurred_at = parsed.astimezone(timezone.utc)
        if (
            now - occurred_at
        ).total_seconds() > LIVE_STUCK_MONITOR_RECOVERY_STALE_SECONDS:
            stale_classified_ids.append(position_id)
            if position_id in fresh_failed_id_set:
                stale_fresh_failed_timestamp_ids.append(position_id)
    # Closed-market entries are deliberately not part of the ordinary stale
    # partition. Validate their timestamp here too, so a historical closed
    # hold cannot authorize a repair restart.
    classified_id_set = {
        str(item.get("position_id") or "").strip()
        for item in classified_positions
        if isinstance(item, dict)
    }
    for item in fresh_failed_positions:
        position_id = str(item.get("position_id") or "").strip()
        if not position_id or position_id in classified_id_set:
            continue
        timestamp_text = str(item.get("last_monitor_refreshed_at") or "").strip()
        if not timestamp_text:
            missing_monitor_timestamp_ids.append(position_id)
            continue
        try:
            parsed = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            invalid_monitor_timestamp_ids.append(position_id)
            continue
        if parsed.tzinfo is None:
            invalid_monitor_timestamp_ids.append(position_id)
            continue
        if (now - parsed.astimezone(timezone.utc)).total_seconds() > (
            LIVE_STUCK_MONITOR_RECOVERY_STALE_SECONDS
        ):
            stale_classified_ids.append(position_id)
            stale_fresh_failed_timestamp_ids.append(position_id)
    identity_complete = (
        len(monitored_ids) == open_count
        and all(monitored_ids)
        and len(set(monitored_ids)) == len(monitored_ids)
    )
    green = bool(
        open_count > 0
        and identity_complete
        and int(cadence.get("non_monitor_chain_risk_position_count") or 0) == 0
        and int(cadence.get("future_monitor_event_count") or 0) == 0
        and restart_blocking_count == 0
        and quote_only_count == 0
        and (
            fresh_count
            + probability_degraded_count
            + reauction_handoff_count
            + len(settlement_recoverable_ids)
            == open_count
        )
    )
    return {
        "green": green,
        "open_position_count": open_count,
        "monitored_position_ids": monitored_ids,
        "fresh_position_count": fresh_count,
        "probability_degraded_position_count": probability_degraded_count,
        "probability_degraded_position_ids": probability_ids,
        "quote_only_stale_position_ids": quote_only_ids,
        "quote_only_stale_shape_valid": quote_only_shape_error is None,
        "quote_only_stale_shape_error": quote_only_shape_error,
        "reauction_handoff_position_count": reauction_handoff_count,
        "reauction_handoff_position_ids": reauction_handoff_ids,
        "restart_blocking_position_count": restart_blocking_count,
        "restart_blocking_position_ids": restart_blocking_ids,
        "settlement_recoverable_position_count": len(settlement_recoverable_ids),
        "settlement_recoverable_position_ids": settlement_recoverable_ids,
        "fresh_failed_monitor_no_action_position_count": len(fresh_failed_ids),
        "fresh_failed_monitor_no_action_position_ids": fresh_failed_ids,
        "fresh_failed_monitor_duplicate_position_ids": fresh_failed_duplicate_ids,
        "fresh_failed_monitor_other_classified_position_ids": fresh_failed_other_ids,
        "fresh_failed_monitor_timestamp_stale_position_ids": tuple(
            stale_fresh_failed_timestamp_ids
        ),
        "stale_classified_position_ids": tuple(stale_classified_ids),
        "missing_monitor_timestamp_position_ids": tuple(missing_monitor_timestamp_ids),
        "invalid_monitor_timestamp_position_ids": tuple(invalid_monitor_timestamp_ids),
        "quote_only_stale_position_count": quote_only_count,
        "future_monitor_event_count": int(
            cadence.get("future_monitor_event_count") or 0
        ),
        "non_monitor_chain_risk_position_count": int(
            cadence.get("non_monitor_chain_risk_position_count") or 0
        ),
        "sample": groups.get("restart_blocking_stale_positions", []),
    }


def _held_quote_sidecar_current_evidence() -> dict[str, object]:
    """Return the non-authorizing current-state proof for held quote refresh.

    ``price-channel-ingest`` writes ``alive_at`` only after a successful M5
    proof.  This admission therefore cannot treat a merely launched sidecar or
    a future-dated heartbeat as current held-quote coverage.
    """

    path = (
        Path(_require_live_repo())
        / "state"
        / "daemon-heartbeat-price-channel-ingest.json"
    )
    payload = _load_json(path)
    observed_at = _parse_iso_utc(payload.get("alive_at"))
    if not payload:
        return {"current": False, "reason": "held_quote_sidecar_unreadable"}
    if payload.get("daemon") != "price-channel-ingest":
        return {"current": False, "reason": "held_quote_sidecar_identity_invalid"}
    if payload.get("status") != "READY" or payload.get("liveness") != "ALIVE":
        return {"current": False, "reason": "held_quote_sidecar_not_ready"}
    if observed_at is None:
        return {"current": False, "reason": "held_quote_sidecar_timestamp_invalid"}
    age_seconds = (datetime.now(timezone.utc) - observed_at).total_seconds()
    if age_seconds < 0.0:
        return {
            "current": False,
            "reason": "held_quote_sidecar_timestamp_future",
            "age_seconds": age_seconds,
        }
    if age_seconds > LIVE_HELD_QUOTE_SIDECAR_MAX_AGE_SECONDS:
        return {
            "current": False,
            "reason": "held_quote_sidecar_stale",
            "age_seconds": age_seconds,
        }
    return {"current": True, "age_seconds": age_seconds}


def _stuck_monitor_recovery_admission(
    *,
    obligations: dict[str, object],
    pause_state: dict[str, object],
    handoff: dict[str, object],
) -> tuple[bool, str]:
    """Admit only a fully classified, quote-supported total monitor stall.

    This is a narrow stop-to-absent recovery, not alternate handoff authority.
    Fresh or partial handoffs retain the ordinary ``green`` gate above.
    """

    open_count = int(obligations.get("open_position_count") or 0)
    command_count = int(obligations.get("nonterminal_command_count") or 0)
    expected_ids = tuple(obligations.get("all_open_position_ids") or ())
    expected_set = {str(position_id).strip() for position_id in expected_ids}
    required_handoff_fields = {
        "open_position_count",
        "fresh_position_count",
        "future_monitor_event_count",
        "non_monitor_chain_risk_position_count",
        "quote_only_stale_position_count",
        "quote_only_stale_position_ids",
        "quote_only_stale_shape_valid",
        "quote_only_stale_shape_error",
        "reauction_handoff_position_count",
        "probability_degraded_position_count",
        "probability_degraded_position_ids",
        "restart_blocking_position_count",
        "restart_blocking_position_ids",
        "settlement_recoverable_position_count",
        "settlement_recoverable_position_ids",
        "stale_classified_position_ids",
        "missing_monitor_timestamp_position_ids",
        "invalid_monitor_timestamp_position_ids",
    }
    if pause_state.get("entries_paused") is not True:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:durable_entries_pause_false"
    if command_count:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:nonterminal_commands"
    if open_count <= 0 or len(expected_ids) != open_count or len(expected_set) != open_count:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:canonical_open_identity_incomplete"
    if not required_handoff_fields.issubset(handoff):
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:handoff_evidence_incomplete"
    if int(handoff.get("open_position_count") or 0) != open_count:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:handoff_open_count_mismatch"
    if int(handoff.get("fresh_position_count") or 0) != 0:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:partial_or_fresh_handoff"
    if int(handoff.get("future_monitor_event_count") or 0) != 0:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:future_monitor_evidence"
    if int(handoff.get("non_monitor_chain_risk_position_count") or 0) != 0:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:non_monitor_chain_risk"
    if handoff.get("quote_only_stale_shape_valid") is not True:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:quote_only_shape_invalid"
    if int(handoff.get("reauction_handoff_position_count") or 0) != 0:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:reauction_handoff_present"
    if handoff.get("missing_monitor_timestamp_position_ids"):
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:monitor_timestamp_missing"
    if handoff.get("invalid_monitor_timestamp_position_ids"):
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:monitor_timestamp_invalid"

    probability_ids = tuple(handoff.get("probability_degraded_position_ids") or ())
    restart_ids = tuple(handoff.get("restart_blocking_position_ids") or ())
    quote_only_ids = tuple(handoff.get("quote_only_stale_position_ids") or ())
    settlement_ids = tuple(
        handoff.get("settlement_recoverable_position_ids") or ()
    )
    classified_ids = (
        *probability_ids,
        *restart_ids,
        *quote_only_ids,
        *settlement_ids,
    )
    classified_set = {str(position_id).strip() for position_id in classified_ids}
    if (
        len(probability_ids)
        != int(handoff.get("probability_degraded_position_count") or 0)
        or len(restart_ids)
        != int(handoff.get("restart_blocking_position_count") or 0)
        or len(quote_only_ids)
        != int(handoff.get("quote_only_stale_position_count") or 0)
        or len(settlement_ids)
        != int(handoff.get("settlement_recoverable_position_count") or 0)
        or not all(str(position_id).strip() for position_id in classified_ids)
        or set(str(position_id).strip() for position_id in probability_ids)
        & set(str(position_id).strip() for position_id in restart_ids)
        or set(str(position_id).strip() for position_id in probability_ids)
        & set(str(position_id).strip() for position_id in quote_only_ids)
        or set(str(position_id).strip() for position_id in restart_ids)
        & set(str(position_id).strip() for position_id in quote_only_ids)
        or set(str(position_id).strip() for position_id in settlement_ids)
        & set(str(position_id).strip() for position_id in probability_ids)
        or set(str(position_id).strip() for position_id in settlement_ids)
        & set(str(position_id).strip() for position_id in restart_ids)
        or set(str(position_id).strip() for position_id in settlement_ids)
        & set(str(position_id).strip() for position_id in quote_only_ids)
        or len(classified_ids) != open_count
        or len(classified_set) != open_count
        or classified_set != expected_set
    ):
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:open_classification_incomplete"
    stale_ids = tuple(handoff.get("stale_classified_position_ids") or ())
    if set(str(position_id).strip() for position_id in stale_ids) != expected_set:
        return False, "STUCK_MONITOR_RECOVERY_REFUSED:monitor_evidence_not_stale"

    quote_sidecar = _held_quote_sidecar_current_evidence()
    if quote_sidecar.get("current") is not True:
        return (
            False,
            "STUCK_MONITOR_RECOVERY_REFUSED:"
            f"{quote_sidecar.get('reason') or 'held_quote_sidecar_not_current'}",
        )
    return (
        True,
        "STUCK_MONITOR_RECOVERY_ADMITTED: "
        f"open_positions={open_count} fresh_positions=0 "
        f"quote_only_stale_positions={len(quote_only_ids)} "
        f"settlement_recoverable_positions={len(settlement_ids)} "
        f"monitor_stale_bound_seconds={LIVE_STUCK_MONITOR_RECOVERY_STALE_SECONDS} "
        "held_quote_sidecar_current=true",
    )


def _loaded_live_runtime_repair_pending() -> dict[str, object]:
    """Prove the loaded process predates the currently checked-out repair."""

    payload = _load_json(Path(_require_live_repo()) / "state" / "loaded_sha.json")
    loaded_sha = str(
        payload.get("loaded_sha") or payload.get("boot_sha") or ""
    ).strip()
    expected_sha = head_sha(short=False)
    if not loaded_sha or not expected_sha or expected_sha == "?":
        return {"pending": False, "reason": "loaded_or_current_sha_unreadable"}
    if _git_head_matches(expected_sha, loaded_sha):
        return {"pending": False, "reason": "loaded_sha_matches_current_head"}
    return {
        "pending": True,
        "loaded_sha": loaded_sha,
        "current_head": expected_sha,
    }


def _fresh_failed_monitor_handoff_shape_valid(
    *,
    obligations: object,
    pause_state: object,
    handoff: object,
    repair_pending: object,
) -> bool:
    """Reject malformed in-memory evidence before restart admission reads it."""

    if not all(
        isinstance(value, dict)
        for value in (obligations, pause_state, handoff, repair_pending)
    ):
        return False

    def _nonnegative_int(value: object) -> bool:
        return type(value) is int and value >= 0

    def _position_ids(value: object) -> bool:
        return isinstance(value, (list, tuple)) and all(
            type(position_id) is str and bool(position_id.strip())
            for position_id in value
        )

    obligation_counts = (
        obligations.get("open_position_count"),
        obligations.get("nonterminal_command_count"),
    )
    handoff_counts = tuple(
        handoff.get(field)
        for field in (
            "open_position_count",
            "fresh_position_count",
            "probability_degraded_position_count",
            "future_monitor_event_count",
            "non_monitor_chain_risk_position_count",
            "quote_only_stale_position_count",
            "reauction_handoff_position_count",
            "fresh_failed_monitor_no_action_position_count",
        )
    )
    handoff_id_fields = tuple(
        handoff.get(field)
        for field in (
            "probability_degraded_position_ids",
            "fresh_failed_monitor_no_action_position_ids",
            "fresh_failed_monitor_duplicate_position_ids",
            "fresh_failed_monitor_other_classified_position_ids",
            "fresh_failed_monitor_timestamp_stale_position_ids",
            "missing_monitor_timestamp_position_ids",
            "invalid_monitor_timestamp_position_ids",
        )
    )
    return bool(
        all(_nonnegative_int(value) for value in (*obligation_counts, *handoff_counts))
        and _position_ids(obligations.get("all_open_position_ids"))
        and all(_position_ids(value) for value in handoff_id_fields)
        and type(pause_state.get("entries_paused")) is bool
        and type(handoff.get("quote_only_stale_shape_valid")) is bool
        and type(repair_pending.get("pending")) is bool
    )


def _fresh_failed_monitor_repair_handoff_admission(
    *,
    obligations: dict[str, object],
    pause_state: dict[str, object],
    handoff: dict[str, object],
    repair_pending: dict[str, object],
) -> tuple[bool, str]:
    """Admit a fully classified fresh no-action monitor failure for restart only.

    SCOPE: stopping an already-loaded live-trading daemon while every canonical
    open position has a current typed no-action monitor result. DRAIN: the
    replacement daemon loads the pending repair and resumes normal monitor
    coverage; this function never grants submit or exit authority. RESET: any
    actionable/future/stale/missing classification, sidecar regression, command,
    pause release, or loaded SHA catching up refuses on the next invocation.
    """

    if not _fresh_failed_monitor_handoff_shape_valid(
        obligations=obligations,
        pause_state=pause_state,
        handoff=handoff,
        repair_pending=repair_pending,
    ):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:handoff_evidence_invalid"

    open_count = int(obligations.get("open_position_count") or 0)
    command_count = int(obligations.get("nonterminal_command_count") or 0)
    expected_ids = tuple(obligations.get("all_open_position_ids") or ())
    expected_set = {str(position_id).strip() for position_id in expected_ids}
    required_handoff_fields = {
        "open_position_count",
        "fresh_position_count",
        "probability_degraded_position_count",
        "probability_degraded_position_ids",
        "future_monitor_event_count",
        "non_monitor_chain_risk_position_count",
        "quote_only_stale_position_count",
        "quote_only_stale_shape_valid",
        "reauction_handoff_position_count",
        "fresh_failed_monitor_no_action_position_count",
        "fresh_failed_monitor_no_action_position_ids",
        "fresh_failed_monitor_duplicate_position_ids",
        "fresh_failed_monitor_other_classified_position_ids",
        "fresh_failed_monitor_timestamp_stale_position_ids",
        "missing_monitor_timestamp_position_ids",
        "invalid_monitor_timestamp_position_ids",
    }
    if pause_state.get("entries_paused") is not True:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:durable_entries_pause_false"
    if command_count:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:nonterminal_commands"
    if open_count <= 0 or len(expected_ids) != open_count or len(expected_set) != open_count:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:canonical_open_identity_incomplete"
    if not required_handoff_fields.issubset(handoff):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:handoff_evidence_incomplete"
    if repair_pending.get("pending") is not True:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:repair_code_not_pending"
    if int(handoff.get("open_position_count") or 0) != open_count:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:handoff_open_count_mismatch"
    fresh_count = int(handoff.get("fresh_position_count") or 0)
    probability_count = int(
        handoff.get("probability_degraded_position_count") or 0
    )
    no_action_count = int(
        handoff.get("fresh_failed_monitor_no_action_position_count") or 0
    )
    if handoff.get("fresh_failed_monitor_duplicate_position_ids"):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:no_action_partition_duplicate"
    if fresh_count + probability_count + no_action_count != open_count:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:open_no_action_partition_incomplete"
    if int(handoff.get("future_monitor_event_count") or 0) != 0:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:future_monitor_evidence"
    if int(handoff.get("non_monitor_chain_risk_position_count") or 0) != 0:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:non_monitor_chain_risk"
    if int(handoff.get("quote_only_stale_position_count") or 0) != 0:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:held_quote_stale"
    if handoff.get("quote_only_stale_shape_valid") is not True:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:quote_only_shape_invalid"
    if int(handoff.get("reauction_handoff_position_count") or 0) != 0:
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:reauction_handoff_present"
    if handoff.get("missing_monitor_timestamp_position_ids"):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:monitor_timestamp_missing"
    if handoff.get("invalid_monitor_timestamp_position_ids"):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:monitor_timestamp_invalid"
    if handoff.get("fresh_failed_monitor_timestamp_stale_position_ids"):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:monitor_timestamp_stale"
    probability_ids = tuple(handoff.get("probability_degraded_position_ids") or ())
    probability_set = {str(position_id).strip() for position_id in probability_ids}
    no_action_ids = tuple(handoff.get("fresh_failed_monitor_no_action_position_ids") or ())
    no_action_set = {str(position_id).strip() for position_id in no_action_ids}
    if (
        len(probability_ids) != probability_count
        or len(probability_set) != probability_count
        or not probability_set.issubset(expected_set)
        or len(no_action_ids) != no_action_count
        or len(no_action_set) != no_action_count
        or not no_action_set.issubset(expected_set)
        or probability_set & no_action_set
    ):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:open_no_action_partition_incomplete"
    other_ids = tuple(
        handoff.get("fresh_failed_monitor_other_classified_position_ids") or ()
    )
    if (
        len(other_ids) != probability_count
        or {str(position_id).strip() for position_id in other_ids}
        != probability_set
    ):
        return False, "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:no_action_partition_not_disjoint"

    quote_sidecar = _held_quote_sidecar_current_evidence()
    if quote_sidecar.get("current") is not True:
        return (
            False,
            "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_REFUSED:"
            f"{quote_sidecar.get('reason') or 'held_quote_sidecar_not_current'}",
        )
    return (
        True,
        "FRESH_FAILED_MONITOR_REPAIR_HANDOFF_ADMITTED: "
        f"open_positions={open_count} fresh_actionable_positions={fresh_count} "
        f"probability_degraded_positions={probability_count} "
        "typed_no_action_partition=complete held_quote_sidecar_current=true "
        "restart_permission_only=true",
    )


def _exact_v4_reauction_restart_handoff_ids(
    conn: sqlite3.Connection,
    *,
    positions: object,
    now: datetime,
) -> tuple[str, ...]:
    """Return exact fresh canonical SELL debts that the new runtime can drain.

    This is a repair handoff, never action authority. It applies only to the
    typed completion defect being deployed: the loaded runtime already wrote a
    fresh q/book MONITOR event and an exact V4 pending outbox debt, but cannot
    bind that event's immutable lineage until the repaired runtime starts.
    """

    candidates = {
        str(item.get("position_id") or "").strip(): str(
            item.get("last_monitor_refreshed_at") or ""
        ).strip()
        for item in positions
        if isinstance(item, dict)
        and item.get("issue") == "monitor_exit_completion_unavailable"
        and str(item.get("position_id") or "").strip()
        and str(item.get("last_monitor_refreshed_at") or "").strip()
    }
    if not candidates:
        return ()
    position_columns = _sqlite_table_columns(conn, "position_current")
    event_columns = _sqlite_table_columns(conn, "position_events")
    if not {
        "position_id",
        "direction",
        "token_id",
        "no_token_id",
    }.issubset(position_columns) or not {
        "event_id",
        "position_id",
        "sequence_no",
        "event_type",
        "occurred_at",
        "payload_json",
    }.issubset(event_columns):
        return ()

    verified: list[str] = []
    now_utc = now.astimezone(timezone.utc)
    for position_id, expected_occurred_at in sorted(candidates.items()):
        row = conn.execute(
            """
            SELECT pe.event_id, pe.occurred_at, pe.payload_json,
                   pc.direction, pc.token_id, pc.no_token_id
              FROM position_events pe
              JOIN position_current pc ON pc.position_id = pe.position_id
             WHERE pe.position_id = ?
               AND pe.event_type = 'MONITOR_REFRESHED'
             ORDER BY pe.sequence_no DESC, datetime(pe.occurred_at) DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        if row is None or str(row[1] or "") != expected_occurred_at:
            continue
        try:
            payload = json.loads(str(row[2] or "{}"))
        except (TypeError, json.JSONDecodeError):
            continue
        obligation = payload.get("held_sell_reauction_obligation")
        validations = payload.get("applied_validations")
        if not isinstance(obligation, dict) or not isinstance(validations, list):
            continue
        validation_set = {str(value) for value in validations}
        request_id = str(obligation.get("request_id") or "").strip()
        direction = str(row[3] or "").strip().lower()
        held_token_id = str(
            row[5] if direction == "buy_no" else row[4] if direction == "buy_yes" else ""
        ).strip()
        try:
            best_bid = float(obligation.get("held_best_bid"))
        except (TypeError, ValueError):
            continue
        deadline = _parse_iso_utc(obligation.get("completion_deadline_at"))
        if (
            payload.get("last_monitor_prob_is_fresh") is not True
            or payload.get("last_monitor_market_price_is_fresh") is not True
            or obligation.get("schema_version") != 4
            or str(obligation.get("position_id") or "").strip() != position_id
            or not held_token_id
            or str(obligation.get("held_token_id") or "").strip()
            != held_token_id
            or str(obligation.get("book_state") or "") != "EXECUTABLE"
            or not math.isfinite(best_bid)
            or not 0.05 <= best_bid <= 0.95
            or not str(
                obligation.get("probability_content_identity") or ""
            ).strip()
            or deadline is None
            or deadline < now_utc
            or not request_id
            or "global_auction_completion_request_failed" not in validation_set
            or "global_auction_completion_debt:REQUEST_REJECTED"
            not in validation_set
            or "GLOBAL_REAUCTION_PENDING" not in validation_set
            or f"global_auction_completion_request_id:{request_id}"
            not in validation_set
        ):
            continue
        verified.append(position_id)
    return tuple(verified)


def _v4_lineage_reauction_restart_handoff_ids(
    conn: sqlite3.Connection,
    *,
    positions: object,
) -> tuple[str, ...]:
    """Pair a fresh failed monitor cut with its exact durable V4 lineage.

    The loaded runtime can refresh q/book faster than it can terminate the
    prior immutable V4 attempt.  A lineage conflict then leaves the latest
    monitor event without an embedded obligation even though its typed lineage
    index remains durable.  The lineage is restart handoff evidence only: the
    new runtime must still rebind current q/book and pass submit-time authority.
    """

    candidates = {
        str(item.get("position_id") or "").strip(): str(
            item.get("last_monitor_refreshed_at") or ""
        ).strip()
        for item in positions
        if isinstance(item, dict)
        and item.get("issue") == "monitor_exit_completion_unavailable"
        and str(item.get("position_id") or "").strip()
        and str(item.get("last_monitor_refreshed_at") or "").strip()
    }
    if not candidates:
        return ()
    position_columns = _sqlite_table_columns(conn, "position_current")
    event_columns = _sqlite_table_columns(conn, "position_events")
    if not {
        "position_id",
        "direction",
        "token_id",
        "no_token_id",
    }.issubset(position_columns) or not {
        "position_id",
        "sequence_no",
        "event_type",
        "occurred_at",
        "payload_json",
    }.issubset(event_columns):
        return ()

    try:
        from src.runtime.reactor_wake import (
            REACTOR_WAKE_FILENAME,
            held_sell_reauction_scope_identity,
            latest_v4_held_sell_reauction_request,
        )

        wake_path = (
            Path(_require_live_repo()) / "state" / REACTOR_WAKE_FILENAME
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        return ()

    verified: list[str] = []
    for position_id, expected_occurred_at in sorted(candidates.items()):
        row = conn.execute(
            """
            SELECT pe.occurred_at, pe.payload_json,
                   pc.direction, pc.token_id, pc.no_token_id
              FROM position_events pe
              JOIN position_current pc ON pc.position_id = pe.position_id
             WHERE pe.position_id = ?
               AND pe.event_type = 'MONITOR_REFRESHED'
             ORDER BY pe.sequence_no DESC, datetime(pe.occurred_at) DESC
             LIMIT 1
            """,
            (position_id,),
        ).fetchone()
        if row is None or str(row[0] or "") != expected_occurred_at:
            continue
        try:
            payload = json.loads(str(row[1] or "{}"))
            probability = float(payload.get("last_monitor_prob"))
            best_bid = float(payload.get("last_monitor_best_bid"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        validations_raw = payload.get("applied_validations")
        validations = (
            {str(value) for value in validations_raw}
            if isinstance(validations_raw, list)
            else set()
        )
        probability_receipt = payload.get("monitor_probability_receipt")
        day0_receipt = payload.get("day0_monitor_probability_receipt")
        monitor_lineage = payload.get("held_sell_reauction_monitor_lineage")
        direction = str(row[2] or "").strip().lower()
        held_token_id = str(
            row[4]
            if direction == "buy_no"
            else row[3]
            if direction == "buy_yes"
            else ""
        ).strip()
        family = (
            str(payload.get("city") or "").strip(),
            str(payload.get("target_date") or "").strip(),
            str(
                payload.get("metric")
                or (
                    day0_receipt.get("metric")
                    if isinstance(day0_receipt, dict)
                    else ""
                )
                or ""
            ).strip().lower(),
        )
        if (
            payload.get("last_monitor_prob_is_fresh") is not True
            or payload.get("last_monitor_market_price_is_fresh") is not True
            or payload.get("held_sell_full_depth_action_authority") is not True
            or not 0.0 <= probability <= 1.0
            or not 0.05 <= best_bid <= 0.95
            or not held_token_id
            or not isinstance(probability_receipt, dict)
            or not str(
                probability_receipt.get("probability_content_identity") or ""
            ).strip()
            or not isinstance(monitor_lineage, dict)
            or not str(
                monitor_lineage.get("selection_epoch_identity") or ""
            ).strip()
            or not str(
                monitor_lineage.get("sell_book_witness_identity") or ""
            ).strip()
            or "sell_reversal" not in validations
            or "global_auction_completion_request_failed" not in validations
            or "global_auction_completion_debt:REQUEST_REJECTED"
            not in validations
            or "GLOBAL_REAUCTION_PENDING" not in validations
        ):
            continue

        if not all(family):
            continue
        probability_content_identity = str(
            probability_receipt.get("probability_content_identity") or ""
        ).strip()
        scope_identity = held_sell_reauction_scope_identity(
            position_id=position_id,
            family=family,
            probability_content_identity=probability_content_identity,
            held_token_id=held_token_id,
            schema_version=4,
        )
        try:
            request = latest_v4_held_sell_reauction_request(
                scope_identity,
                path=wake_path,
            )
        except (OSError, TypeError, ValueError):
            continue
        if request is None:
            continue
        try:
            request_bid = float(getattr(request, "held_best_bid", None))
        except (TypeError, ValueError):
            continue
        if (
            int(getattr(request, "schema_version", 0) or 0) != 4
            or getattr(request, "lineage_status", "") != "COMPLETE"
            or str(getattr(request, "scope_identity", "") or "")
            != scope_identity
            or str(getattr(request, "position_id", "") or "") != position_id
            or str(getattr(request, "held_token_id", "") or "")
            != held_token_id
            or tuple(getattr(request, "family", ()) or ()) != family
            or str(getattr(request, "book_state", "") or "")
            != "EXECUTABLE"
            or not 0.05 <= request_bid <= 0.95
            or not all(
                str(getattr(request, field, "") or "").strip()
                for field in (
                    "request_id",
                    "material_identity",
                    "attempt_identity",
                    "scope_identity",
                    "generation",
                    "probability_content_identity",
                    "selection_epoch_identity",
                    "sell_book_witness_identity",
                    "debt_event_id",
                    "monitor_event_id",
                )
            )
        ):
            continue
        verified.append(position_id)
    return tuple(verified)


def _loaded_live_restart_obligation_gate(
    labels: list[str],
    *,
    live_was_loaded: bool,
) -> tuple[bool, str]:
    """Refuse to create a monitoring blackout over live capital.

    SCOPE: a requested restart that would stop an already-loaded live-trading
    daemon. DRAIN: venue commands reach terminal states; open positions require
    a pre-existing durable entry pause plus complete recent monitor/held-quote
    handoff evidence. RESET: the next invocation re-reads WORLD pause and TRADE
    obligations/evidence; missing, stale, or quote-blind evidence refuses.
    Bootstrapping an already-absent daemon remains allowed because it restores
    monitoring rather than interrupting it.
    """

    if LIVE_TRADING_LABEL not in labels:
        return True, "live restart obligation gate not required for this daemon"
    if not live_was_loaded:
        return True, "live restart obligation gate permits absent-daemon recovery"
    trade_db = Path(_require_live_repo()) / "state" / "zeus_trades.db"
    try:
        obligations = _canonical_live_restart_obligations(trade_db)
    except RuntimeError as exc:
        return False, f"canonical live restart obligations unreadable: {exc}"
    open_count = int(obligations["open_position_count"])
    command_count = int(obligations["nonterminal_command_count"])
    if command_count:
        return (
            False,
            "loaded live-trading restart would interrupt capital monitoring: "
            f"open_positions={open_count} "
            f"nonterminal_commands={command_count} "
            f"position_sample={list(obligations['open_position_ids'])} "
            f"command_sample={list(obligations['nonterminal_command_ids'])}",
        )
    if open_count:
        world_db = Path(_require_live_repo()) / "state" / "zeus-world.db"
        try:
            pause_state = _durable_entries_pause_state(world_db)
        except RuntimeError as exc:
            return False, f"loaded live-trading repair handoff pause unreadable: {exc}"
        if pause_state.get("entries_paused") is not True:
            return (
                False,
                "loaded live-trading restart would interrupt capital monitoring: "
                f"open_positions={open_count} durable_entries_pause=false "
                f"position_sample={list(obligations['open_position_ids'])}",
            )
        handoff = _pre_stop_monitor_handoff_evidence(trade_db)
        if (
            handoff.get("green") is not True
            or int(handoff.get("open_position_count") or 0) != open_count
        ):
            stuck_ok, stuck_detail = _stuck_monitor_recovery_admission(
                obligations=obligations,
                pause_state=pause_state,
                handoff=handoff,
            )
            if stuck_ok:
                return True, f"loaded live-trading {stuck_detail}"
            fresh_failed_ok, fresh_failed_detail = (
                _fresh_failed_monitor_repair_handoff_admission(
                    obligations=obligations,
                    pause_state=pause_state,
                    handoff=handoff,
                    repair_pending=_loaded_live_runtime_repair_pending(),
                )
            )
            if fresh_failed_ok:
                return True, f"loaded live-trading {fresh_failed_detail}"
            return (
                False,
                "loaded live-trading repair handoff is not current: "
                f"open_positions={open_count} evidence={handoff} "
                f"stuck_monitor_recovery={stuck_detail} "
                f"fresh_failed_monitor_repair_handoff={fresh_failed_detail}",
            )
        return (
            True,
            "loaded live-trading repair handoff verified: "
            f"open_positions={open_count} nonterminal_commands=0 "
            "durable_entries_pause=true "
            "full_book_recent_held_quotes=true "
            "probability_degraded_positions="
            f"{int(handoff.get('probability_degraded_position_count') or 0)} "
            "exact_v4_reauction_handoffs="
            f"{int(handoff.get('reauction_handoff_position_count') or 0)}",
        )
    return (
        True,
        "loaded live-trading restart obligation gate verified: "
        "open_positions=0 nonterminal_commands=0",
    )


def _wait_for_loaded_live_restart_handoff(
    labels: list[str],
    *,
    timeout_seconds: float = LIVE_PRESTOP_HANDOFF_VERIFY_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Wait while the loaded daemon restores a safe post-sidecar handoff.

    Reloading prerequisite book/ingest daemons can briefly age held-side quote
    evidence. The old live-trading process is still loaded, entries are already
    durably paused, and no stop has happened, so the safe action is to wait for
    its next complete monitor pass. The stop gate itself is unchanged: timeout
    or any persistent command/quote/identity defect still refuses the restart.
    """

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    last_detail = "not checked"
    while True:
        ok, detail = _loaded_live_restart_obligation_gate(
            labels,
            live_was_loaded=True,
        )
        if ok:
            return True, detail
        last_detail = detail
        if time.monotonic() >= deadline:
            return False, last_detail
        time.sleep(LIVE_RUNTIME_FRESH_VERIFY_POLL_SECONDS)


def _durable_entries_pause_state(world_db: Path) -> dict[str, object]:
    """Read the current WORLD pause authority from a separate read-only handle."""

    try:
        conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        required_columns = {
            "override_id", "target_type", "target_key", "action_type", "value",
            "issued_by", "issued_at", "effective_until", "reason", "precedence",
        }
        try:
            columns = _sqlite_table_columns(conn, "control_overrides")
        except RuntimeError as exc:
            raise RuntimeError(
                "EDLI_EXPECTED_PARKED_PAUSE_UNREADABLE:missing_table"
            ) from exc
        if not required_columns.issubset(columns):
            raise RuntimeError(
                "EDLI_EXPECTED_PARKED_PAUSE_UNREADABLE:projection_columns"
            )
        return dict(query_control_override_state(conn))
    except sqlite3.Error as exc:
        raise RuntimeError("EDLI_EXPECTED_PARKED_PAUSE_UNREADABLE:sqlite_error") from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass


def _nonterminal_sell_command_count(trade_db: Path) -> int:
    """Count canonical SELL commands still able to require venue recovery."""

    from src.execution.command_bus import TERMINAL_STATES

    try:
        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        columns = _sqlite_table_columns(conn, "venue_commands")
        if not {"side", "state"}.issubset(columns):
            raise RuntimeError("EDLI_EXPECTED_PARKED_COMMAND_PROJECTION_UNREADABLE")
        terminal_states = sorted(
            {state.value for state in TERMINAL_STATES} | {"CANCELED", "FAILED"}
        )
        placeholders = ", ".join("?" for _ in terminal_states)
        row = conn.execute(
            f"""
            SELECT COUNT(*)
              FROM venue_commands
             WHERE UPPER(COALESCE(side, '')) = 'SELL'
               AND UPPER(COALESCE(state, '')) NOT IN ({placeholders})
            """,
            tuple(terminal_states),
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error as exc:
        raise RuntimeError("EDLI_EXPECTED_PARKED_COMMAND_PROJECTION_UNREADABLE") from exc
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass


def _held_sell_global_auction_debt_count(state_dir: Path) -> int:
    """Return exact held-SELL debt using the reactor's public queue semantics."""

    from src.runtime.reactor_wake import (
        GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        REACTOR_WAKE_FILENAME,
        REACTOR_WAKE_QUEUE_SUFFIX,
        _read_reactor_wake_path,
    )

    wake_path = state_dir / REACTOR_WAKE_FILENAME
    queue_dir = wake_path.with_name(f"{wake_path.name}{REACTOR_WAKE_QUEUE_SUFFIX}")
    wakes = _strict_reactor_wake_snapshot(
        wake_path=wake_path,
        queue_dir=queue_dir,
        read_wake=_read_reactor_wake_path,
    )
    return len(
        {
            wake.wake_id
            for wake in wakes
            if (
                wake.reason == GLOBAL_AUCTION_COMPLETION_WAKE_REASON
                and wake.held_sell_reauction_requests
            )
        }
    )


def _strict_reactor_wake_snapshot(
    *,
    wake_path: Path,
    queue_dir: Path,
    read_wake,
):
    """Read each present wake file once; missing surfaces are a valid empty queue."""

    def validate(wake_file: Path):
        try:
            if not stat.S_ISREG(wake_file.stat().st_mode):
                raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_INVALID")
            # Public snapshots intentionally suppress parse failures.  Reuse the
            # reactor's strict parser so acceptance cannot turn corruption into zero debt.
            wake = read_wake(wake_file, fail_on_error=True)
        except OSError as exc:
            raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_UNREADABLE") from exc
        except ValueError as exc:
            raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_INVALID") from exc
        if wake is None:
            raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_UNREADABLE")
        return wake

    wakes = []
    try:
        wake_path.stat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_UNREADABLE") from exc
    else:
        wakes.append(validate(wake_path))

    try:
        if not stat.S_ISDIR(queue_dir.stat().st_mode):
            raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_INVALID")
        queue_files = tuple(
            path for path in queue_dir.iterdir() if path.suffix == ".json"
        )
    except FileNotFoundError:
        return tuple(wakes)
    except OSError as exc:
        raise RuntimeError("EDLI_EXPECTED_PARKED_WAKE_SURFACE_UNREADABLE") from exc
    for queue_file in queue_files:
        wakes.append(validate(queue_file))
    return tuple(wakes)


def _sqlite_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = {str(row[1]) for row in rows}
    if not columns:
        raise RuntimeError(f"EDLI_EXPECTED_PARKED_TABLE_MISSING:{table}")
    return columns


def _latest_complete_global_auction_receipt(
    trade_db: Path,
    *,
    launched_floor: datetime,
    require_held_coverage_count: int = 0,
    require_held_position_ids: tuple[str, ...] = (),
) -> tuple[int, int, int] | None:
    """Return a post-launch complete auction as direct reactor progress proof."""

    if not trade_db.exists():
        return None
    try:
        from src.ops.monitor_cadence import latest_complete_global_auction_receipt

        conn = sqlite3.connect(f"file:{trade_db}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            return latest_complete_global_auction_receipt(
                conn,
                completed_not_before=launched_floor,
                require_held_coverage_count=require_held_coverage_count,
                require_held_position_ids=require_held_position_ids,
            )
        finally:
            conn.close()
    except Exception:
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
        if not _wait_for_launchctl_unloaded(label):
            return False, f"FAILED stop {label}: service still loaded after bootout"
        return True, f"stopped {label}"
    return False, f"FAILED stop {label}: rc={stop.returncode} {stop.stderr.strip()}"


def _run_restart_recovery_with_quiesced_prerequisites(
    labels: list[str],
    prerequisite_labels: list[str],
    *,
    expected_sha: str,
) -> tuple[bool, str]:
    """Give canonical restart recovery an exclusive writer interval.

    Live prerequisites are recurring DB writers.  Merely stopping the order
    daemon leaves recovery racing those sidecars for SQLite's single writer.
    Quiesce the already-verified prerequisites, run recovery, then restore and
    re-verify every sidecar before returning.  Restoration is unconditional:
    recovery failure keeps trading stopped, not the source/data mesh.
    """

    details: list[str] = []
    quiesced: list[str] = []
    quiesce_ok = True
    for label in prerequisite_labels:
        ok, detail = _stop_label(label)
        details.append(detail)
        if ok:
            quiesced.append(label)
        else:
            quiesce_ok = False
            break

    if quiesce_ok:
        recovery_ok, recovery_detail = _run_restart_recovery_if_needed(labels)
    else:
        recovery_ok = False
        recovery_detail = "live restart recovery not run: prerequisite quiesce failed"
    details.append(recovery_detail)

    restore_started_at = datetime.now(timezone.utc)
    restore_ok = True
    for label in quiesced:
        ok, detail = _launch_or_restart_label(label)
        details.append(detail)
        if not ok:
            restore_ok = False
    if restore_ok:
        identity_ok, identity_detail = _wait_for_prerequisite_code_identity(
            quiesced,
            expected_sha=expected_sha,
            launched_after=restore_started_at,
        )
        details.append(identity_detail)
        restore_ok = identity_ok

    return recovery_ok and restore_ok, "\n".join(details)


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


def _run_restart_preflight_if_needed(
    labels: list[str],
    *,
    expected_live_process_state: str = "absent",
    process_state_only: bool = False,
    defer_running_monitor_cadence: bool = False,
) -> tuple[bool, str]:
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
    cmd = [
        py,
        "scripts/check_live_restart_preflight.py",
        "--json",
        "--expected-live-process-state",
        expected_live_process_state,
    ]
    if process_state_only:
        cmd.append("--process-state-only")
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
    try:
        payload = json.loads(output)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        if res.returncode == 0:
            return False, f"live restart preflight returned invalid JSON: {exc}"
        tail = "\n".join(output.splitlines()[-80:]) if output else "<no output>"
        return False, f"live restart preflight failed rc={res.returncode}:\n{tail}"
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), list):
        if res.returncode != 0:
            tail = "\n".join(output.splitlines()[-80:]) if output else "<no output>"
            return False, f"live restart preflight failed rc={res.returncode}:\n{tail}"
        return False, "live restart preflight returned invalid attestation shape"
    reported_process_state = payload.get("expected_live_process_state")
    if reported_process_state is None and expected_live_process_state == "absent":
        reported_process_state = "absent"
    if reported_process_state != expected_live_process_state:
        return False, "live restart preflight process-state attestation mismatch"
    if process_state_only:
        if res.returncode == 0 and payload.get("ok") is True:
            return True, f"live process-state witness passed: {expected_live_process_state}"
        tail = "\n".join(output.splitlines()[-80:]) if output else "<no output>"
        return False, f"live restart preflight failed rc={res.returncode}:\n{tail}"
    price_band = next(
        (
            check
            for check in payload["checks"]
            if isinstance(check, dict)
            if check.get("name") == "absolute_live_unit_price_band"
        ),
        None,
    )
    if not isinstance(price_band, dict):
        return False, (
            "live restart preflight omitted required "
            "absolute_live_unit_price_band attestation"
        )
    if price_band.get("ok") is not True or price_band.get("restart_blocking") is not True:
        return False, (
            "live restart preflight absolute_live_unit_price_band attestation "
            f"is not restart-blocking PASS: {price_band}"
        )
    if res.returncode == 0 and payload.get("ok") is True:
        return True, "live restart preflight passed"
    blockers = payload.get("blockers")
    monitor_only = (
        defer_running_monitor_cadence
        and expected_live_process_state == "running"
        and res.returncode != 0
        and payload.get("ok") is False
        and isinstance(blockers, list)
        and bool(blockers)
        and all(
            isinstance(blocker, dict)
            and blocker.get("name") == "monitor_cadence_restart_evidence"
            and blocker.get("restart_blocking") is True
            for blocker in blockers
        )
    )
    if monitor_only:
        return (
            True,
            "warm preflight passed except monitor cadence; exact current-capital "
            "handoff remains mandatory immediately before stop",
        )
    tail = "\n".join(output.splitlines()[-80:]) if output else "<no output>"
    return False, f"live restart preflight failed rc={res.returncode}:\n{tail}"


def _restart_runtime_db_paths() -> tuple[Path, Path]:
    """Resolve the exact world/trade DBs used by the loaded LaunchAgent."""

    env = _live_trading_subprocess_env()
    live_repo = Path(_require_live_repo()).expanduser().resolve()

    def resolve_from_live(value: str | os.PathLike[str]) -> Path:
        path = Path(value).expanduser()
        return (path if path.is_absolute() else live_repo / path).resolve()

    state_override = env.get("ZEUS_LIVE_PREFLIGHT_STATE_DIR") or env.get(
        "ZEUS_STATE_DIR"
    )
    if state_override:
        state_dir = resolve_from_live(state_override)
    elif env.get("ZEUS_PRIMARY_ROOT"):
        state_dir = resolve_from_live(env["ZEUS_PRIMARY_ROOT"]) / "state"
    else:
        state_dir = live_repo / "state"
    world_override = env.get("ZEUS_WORLD_DB")
    trade_override = env.get("ZEUS_TRADE_DB")
    world_db = (
        resolve_from_live(world_override)
        if world_override
        else (state_dir / "zeus-world.db").resolve()
    )
    trade_db = (
        resolve_from_live(trade_override)
        if trade_override
        else (state_dir / "zeus_trades.db").resolve()
    )
    return world_db, trade_db


def _restart_migration_targets_current() -> tuple[bool, str]:
    """Prove process-absent migrations are already recorded in live DBs."""

    try:
        world_db, trade_db = _restart_runtime_db_paths()
        evidence = []
        for db_path, targets in (
            (world_db, RESTART_WORLD_MIGRATION_TARGETS),
            (trade_db, RESTART_TRADE_MIGRATION_TARGETS),
        ):
            if not db_path.is_file():
                return False, f"restart migration DB missing: {db_path}"
            uri = f"file:{db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            try:
                applied = {
                    str(row[0])
                    for row in conn.execute("SELECT name FROM _migrations_applied")
                }
            finally:
                conn.close()
            missing = [target for _key, target in targets if target not in applied]
            if missing:
                return False, f"restart migrations pending in {db_path}: {missing}"
            evidence.append(f"{db_path}:{len(targets)}")
        return True, "restart migration ledgers current: " + ", ".join(evidence)
    except Exception as exc:  # noqa: BLE001 -- fast path must fail closed.
        return False, f"restart migration ledger proof failed: {type(exc).__name__}: {exc}"


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


def _assert_restart_trade_schema_ready(conn: sqlite3.Connection) -> None:
    """Fail closed on restart unless trade schema metadata is already complete."""

    if conn.in_transaction:
        raise RuntimeError("restart trade schema assertion requires no open transaction")
    from src.execution.settlement_commands import assert_settlement_schema_ready
    from src.state.table_registry import DBIdentity, assert_db_matches_registry

    assert_db_matches_registry(conn, DBIdentity.TRADE)
    assert_settlement_schema_ready(conn)

    def _object(name: str, kind: str) -> str:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
            (kind, name),
        ).fetchone()
        return str(row[0] or "") if row else ""

    history_sql = _object("token_suppression_history", "table")
    if "chain_only_auto_resolved_match" not in history_sql:
        raise RuntimeError("restart trade schema missing token suppression history reason")
    history_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(token_suppression_history)")
    }
    required_history_columns = {
        "history_id", "token_id", "suppression_reason", "operation", "recorded_at"
    }
    if not required_history_columns.issubset(history_columns):
        raise RuntimeError("restart trade schema token suppression history shape is incomplete")

    token_table_sql = _object("token_suppression", "table")
    token_view_sql = _object("token_suppression", "view")
    current_view_sql = _object("token_suppression_current", "view")
    if token_table_sql:
        token_sql = token_table_sql
    elif token_view_sql and current_view_sql:
        if (
            "token_suppression_current" not in token_view_sql
            or "token_suppression_history" not in current_view_sql
        ):
            raise RuntimeError("restart trade schema token suppression view lineage is invalid")
        token_sql = token_view_sql + current_view_sql
    else:
        raise RuntimeError("restart trade schema token suppression table/view shape is invalid")
    if token_table_sql and "chain_only_auto_resolved_match" not in token_sql:
        raise RuntimeError("restart trade schema missing token suppression reason")

    migration = conn.execute(
        "SELECT 1 FROM _migrations_applied WHERE name = ?",
        ("202607_cas_reservation_ledger",),
    ).fetchone()
    if migration is None:
        raise RuntimeError("restart trade schema migration ledger is incomplete")
    if conn.in_transaction:
        raise RuntimeError("restart trade schema assertion opened a transaction")


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
        import sqlite3
        import time
        from scripts.migrations import apply_migrations
        from scripts.deploy_live import (
            RESTART_TRADE_MIGRATION_TARGETS,
            RESTART_WORLD_MIGRATION_TARGETS,
            _assert_restart_trade_schema_ready,
            _ensure_restart_world_schemas,
        )
        from src.state.db import (
            get_trade_connection,
            get_world_connection,
            get_world_connection_read_only,
            get_world_connection_with_trades_required,
        )
        from src.state.schema.opportunity_event_processing_schema import (
            assert_active_projection_ready,
        )
        applied = {}

        world_conn = get_world_connection(write_class='live')
        try:
            _ensure_restart_world_schemas(world_conn)
            for result_key, target in RESTART_WORLD_MIGRATION_TARGETS:
                applied[result_key] = apply_migrations(
                    world_conn,
                    target=target,
                    db_identity='world',
                )
            world_conn.commit()
        finally:
            world_conn.close()

        # The projection reader is never allowed to start on merely-created
        # objects: prove the migration committed its complete active-set receipt
        # from a read-only connection before any daemon is bootstrapped.
        world_ro = get_world_connection_read_only()
        try:
            backfill_columns = {
                str(row[1]): int(row[3])
                for row in world_ro.execute(
                    "PRAGMA table_info(opportunity_event_processing_type_backfill)"
                )
            }
            if backfill_columns.get('consumer_name') != 1:
                raise RuntimeError('EDLI_BACKFILL_RECEIPT_CONSUMER_NOTNULL_REQUIRED')
            seeded_active_count, seed_high_water_rowid = assert_active_projection_ready(
                world_ro,
                consumer_name='edli_reactor_v1',
            )
            receipt = world_ro.execute(
                "SELECT completed_at "
                "FROM opportunity_event_processing_type_backfill "
                "WHERE consumer_name = 'edli_reactor_v1'"
            ).fetchone()
            if receipt is None:
                raise RuntimeError('EDLI_ACTIVE_REDECISION_PROJECTION_UNSEEDED')
            applied['world_active_redecision_projection_receipt'] = {
                'seeded_active_count': seeded_active_count,
                'seed_high_water_rowid': seed_high_water_rowid,
                'completed_at': str(receipt[0]),
            }
            applied['world_active_redecision_backfill_notnull_receipt'] = {
                'consumer_name_notnull': True,
            }
        finally:
            world_ro.close()

        trade_conn = get_trade_connection(write_class='live')
        try:
            for result_key, target in RESTART_TRADE_MIGRATION_TARGETS:
                applied[result_key] = apply_migrations(
                    trade_conn,
                    target=target,
                    db_identity='trade',
                )
            _assert_restart_trade_schema_ready(trade_conn)
        finally:
            trade_conn.close()

        from src.execution.command_recovery import reconcile_unresolved_commands
        from src.events.edli_trade_fact_bridge import (
            append_confirmed_trade_facts_to_edli,
            append_prepared_trade_fact_bridge_evidence,
        )
        from src.ingest.price_channel_ingest import (
            _edli_trade_fact_bridge_candidates_read_only,
        )

        recovery_deadline_monotonic = time.monotonic() + 60.0
        summary = reconcile_unresolved_commands(
            scope='restart_preflight',
            deadline_monotonic=recovery_deadline_monotonic,
        )
        (
            confirmed_candidates,
            rest_orphan_candidates,
            absorbed_fill_aggregate_ids,
        ) = _edli_trade_fact_bridge_candidates_read_only()
        bridge_conn = get_world_connection_with_trades_required(write_class='live')
        try:
            bridge_deadline_monotonic = time.monotonic() + 15.0
            bridge_conn.execute('PRAGMA busy_timeout = 0')
            bridge_conn.set_progress_handler(
                lambda: int(time.monotonic() >= bridge_deadline_monotonic),
                1000,
            )
            try:
                summary['confirmed_fill_bridge_appended'] = 0
                summary['rest_fill_orphan_bridge_appended'] = 0
                for evidence in confirmed_candidates:
                    summary['confirmed_fill_bridge_appended'] += (
                        append_prepared_trade_fact_bridge_evidence(
                            bridge_conn, evidence
                        )
                    )
                for evidence in rest_orphan_candidates:
                    summary['rest_fill_orphan_bridge_appended'] += (
                        append_prepared_trade_fact_bridge_evidence(
                            bridge_conn, evidence
                        )
                    )
                summary['confirmed_fill_bridge_appended'] += append_confirmed_trade_facts_to_edli(
                    bridge_conn,
                    candidates=(),
                    absorbed_fill_aggregate_ids=absorbed_fill_aggregate_ids,
                )
                bridge_conn.commit()
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if not (
                    time.monotonic() >= bridge_deadline_monotonic
                    or 'locked' in message
                    or 'busy' in message
                    or 'interrupted' in message
                ):
                    raise
                bridge_conn.rollback()
                summary['edli_trade_fact_bridge_deferred'] = True
                summary['edli_trade_fact_bridge_deferred_reason'] = 'db_budget_or_contention'
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
    stdout = (res.stdout or "").strip()
    stderr = (res.stderr or "").strip()
    diagnostic = "\n".join(part for part in (stderr, stdout) if part)
    tail = "\n".join(diagnostic.splitlines()[-80:]) if diagnostic else "<no output>"
    if res.returncode != 0:
        return False, f"live restart recovery failed rc={res.returncode}:\n{tail}"
    try:
        summary = json.loads(stdout.splitlines()[-1])
    except Exception:
        summary = {}
    return True, f"live restart recovery passed: {json.dumps(summary, sort_keys=True)}"


def _pause_entries_for_live_restart_if_needed(
    labels: list[str],
    *,
    expected_sha: str | None = None,
) -> tuple[bool, str]:
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
    expected_literal = json.dumps(str(expected_sha or head_sha(short=False)))
    code = textwrap.dedent(
        f"""
        from src.control.control_plane import arm_deploy_live_restart_guard

        # Existing operator/risk/source pauses remain selected and untouched.
        # The guard is indefinite (effective_until=None), never a TTL.
        result = arm_deploy_live_restart_guard(expected_sha={expected_literal})
        if result.get('status') == 'preserved':
            print(
                'entries pause guard preserved: '
                f"issued_by={{result.get('issued_by')}} "
                f"reason={{result.get('reason')}}"
            )
        elif result.get('status') == 'armed':
            witness = result.get('witness') or {{}}
            print(
                'entries pause guard armed: '
                f"expected_sha={{witness.get('expected_sha')}} "
                f"issued_at={{witness.get('issued_at')}}"
            )
        else:
            raise RuntimeError(f"restart guard arm refused: {{result}}")
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


def _pause_entries_with_stuck_live_recovery(
    labels: list[str],
    *,
    live_was_loaded: bool,
    expected_sha: str | None = None,
) -> tuple[bool, str]:
    """Arm the restart guard after stopping requested daemons that hold the writer.

    Any daemon in a live restart scope can hold the world DB writer longer than
    the guard timeout. Stop live-trading first, then only the other labels that
    this deploy already intends to restart. With live entry authority absent it
    is safe to retry the durable pause before any daemon is started again.
    Other pause failures remain fail-closed, with every stopped label left down.
    """

    if expected_sha is None:
        ok, detail = _pause_entries_for_live_restart_if_needed(labels)
    else:
        ok, detail = _pause_entries_for_live_restart_if_needed(
            labels,
            expected_sha=expected_sha,
        )
    writer_stuck = "timed out after" in detail or "database is locked" in detail
    if ok or not writer_stuck:
        return ok, detail
    stop_order = _dedupe_labels([LIVE_TRADING_LABEL, *labels])
    stop_details: list[str] = []
    for label in stop_order:
        stopped, stop_detail = _stop_label(label)
        stop_details.append(stop_detail)
        if not stopped:
            return False, f"{detail}\n" + "; ".join(stop_details)
        if not _wait_for_launchctl_unloaded(label):
            stop_details.append(f"FAILED unload wait {label}")
            return False, f"{detail}\n" + "; ".join(stop_details)
    if expected_sha is None:
        retry_ok, retry_detail = _pause_entries_for_live_restart_if_needed(labels)
    else:
        retry_ok, retry_detail = _pause_entries_for_live_restart_if_needed(
            labels,
            expected_sha=expected_sha,
        )
    prior_state = "loaded" if live_was_loaded else "already absent"
    return (
        retry_ok,
        f"{detail}\nlive-trading was {prior_state}; "
        f"{'; '.join(stop_details)}; pause guard retry after requested daemon absence: "
        f"{retry_detail}",
    )


def _resume_entries_after_verified_live_restart_if_needed(
    labels: list[str],
) -> tuple[bool, str]:
    """CAS-reset only this invocation's guard after sync post-start proofs."""

    if LIVE_TRADING_LABEL not in labels:
        return True, "entry resume not required for this daemon"
    live_repo = _require_live_repo()
    py = os.path.join(live_repo, ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable
    code = textwrap.dedent(
        """
        from src.control.control_plane import recover_deploy_live_restart_guard

        result = recover_deploy_live_restart_guard()
        status = result.get('status')
        if status == 'reset':
            print('deploy live restart guard cleared by shared proof-driven CAS reset')
        elif status == 'noop':
            print(f"entries pause guard preserved after deploy: {result.get('reason')}")
        else:
            print(f"deploy live restart guard retained: {result}")
            raise SystemExit(1)
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


def _restore_paused_live_monitoring_after_failed_restart(
    *,
    post_live_labels: list[str],
    expected_sha: str,
) -> tuple[bool, str]:
    """Restore held-capital monitoring without clearing the restart pause.

    Recovery and preflight failures must block new entries, but they must not
    strand already-held capital without ``src.main`` or its independent
    launchd watchdog.  Start both services, then prove the loaded process and
    held-position monitor advance.  The durable deploy guard remains armed;
    only the fully verified success path may clear it.
    """

    details: list[str] = []
    launched_after = datetime.now(timezone.utc)
    live_ok, live_detail = _launch_or_restart_label(LIVE_TRADING_LABEL)
    details.append(live_detail)

    watchdog_ok = True
    for label in post_live_labels:
        ok, detail = _launch_or_restart_label(label)
        details.append(detail)
        watchdog_ok = watchdog_ok and ok

    runtime_ok = False
    monitor_ok = False
    if live_ok:
        runtime_ok, runtime_detail = _wait_for_live_runtime_fresh(
            expected_sha=expected_sha,
            launched_after=launched_after,
        )
        details.append(runtime_detail)
        if runtime_ok:
            monitor_ok, monitor_detail = _wait_for_post_start_monitor_cadence(
                launched_after=launched_after,
            )
            details.append(monitor_detail)

    restored = live_ok and watchdog_ok and runtime_ok and monitor_ok
    posture = (
        "paused live monitoring restored"
        if restored
        else "paused live monitoring restoration incomplete"
    )
    return restored, f"{posture}; deploy entry guard remains armed:\n" + "\n".join(details)


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
    expected_live_sha = head_sha(short=False) if includes_live_trading else ""

    # Arm the durable entry guard while the loaded main still monitors held
    # capital.  The obligation gate below requires this witness, so checking it
    # first creates a circular refusal.  Do not use the writer-stuck recovery
    # helper here: its stop fallback is only safe after a fresh capital handoff
    # has already been proven.
    pause_ok, pause_detail = _pause_entries_for_live_restart_if_needed(
        labels,
        expected_sha=expected_live_sha,
    )
    if not pause_ok:
        print("REFUSING to restart — live entry pause guard is not armed:")
        print(pause_detail)
        return 1
    print(pause_detail)

    obligation_ok, obligation_detail = _loaded_live_restart_obligation_gate(
        labels,
        live_was_loaded=live_was_loaded_before,
    )
    if not obligation_ok:
        print("REFUSING to restart — live capital still requires continuous monitoring:")
        print(obligation_detail)
        return 1
    print(obligation_detail)

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
    reusable_prerequisite_labels = _current_prerequisite_code_identity_labels(
        preflight_prerequisite_labels,
        expected_sha=expected_live_sha,
    )
    prerequisite_launch_started_at = datetime.now(timezone.utc)
    continuous_monitor_cutover = False
    for label in preflight_prerequisite_labels:
        if label in reusable_prerequisite_labels:
            print(f"retained current prerequisite {label}: loaded SHA and heartbeat verified")
            continue
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
                "live-trading left running with entries paused; fix prerequisite daemon startup before retrying.",
                file=sys.stderr,
            )
            return 1
        print(prerequisite_detail)

        if live_was_loaded_before:
            migrations_current, migration_detail = _restart_migration_targets_current()
            print(migration_detail)
            if migrations_current:
                warm_ok, warm_detail = _run_restart_preflight_if_needed(
                    labels,
                    expected_live_process_state="running",
                    defer_running_monitor_cadence=True,
                )
                if not warm_ok:
                    print(
                        "REFUSING to stop live-trading — warm restart preflight is not green:"
                    )
                    print(warm_detail)
                    return 1
                print(warm_detail)
                continuous_monitor_cutover = True

        # Keep the currently loaded order daemon monitoring held capital while
        # new-code prerequisites become ready.  Stopping it before sidecar
        # reload/identity waits created multi-minute MONITOR_REFRESHED blackouts
        # during which Day0 evidence and executable bids could both move.  The
        # process-absent window starts only after every prerequisite is ready.
        if live_was_loaded_before:
            handoff_ok, handoff_detail = _wait_for_loaded_live_restart_handoff(
                labels,
            )
            if not handoff_ok:
                print(
                    "REFUSING to stop live-trading — capital handoff changed "
                    "during prerequisite reload:"
                )
                print(handoff_detail)
                return 1
            print(f"pre-stop {handoff_detail}")
            if continuous_monitor_cutover:
                running_ok, running_detail = _run_restart_preflight_if_needed(
                    labels,
                    expected_live_process_state="running",
                    process_state_only=True,
                )
                print(running_detail)
                if not running_ok:
                    print(
                        "REFUSING to stop live-trading — exact one-main witness "
                        "failed after the fresh capital handoff",
                        file=sys.stderr,
                    )
                    return 1
        ok, detail = _stop_label(LIVE_TRADING_LABEL)
        if ok:
            print(detail)
        else:
            print(detail, file=sys.stderr)
            return 1
        for label in post_live_labels:
            ok, detail = _stop_label(label)
            if ok:
                print(detail)
            else:
                print(detail, file=sys.stderr)
                return 1
        if continuous_monitor_cutover:
            absent_ok, absent_detail = _run_restart_preflight_if_needed(
                labels,
                expected_live_process_state="absent",
                process_state_only=True,
            )
            print(absent_detail)
            if not absent_ok:
                print(
                    "REFUSING to bootstrap live-trading — exact zero-main witness failed; "
                    "entry pause remains armed and no second main will be launched",
                    file=sys.stderr,
                )
                return 1

    if continuous_monitor_cutover:
        recovery_ok, recovery_detail = (
            True,
            "live restart recovery skipped: migration ledgers and warm preflight current",
        )
    elif includes_live_trading:
        recovery_ok, recovery_detail = (
            _run_restart_recovery_with_quiesced_prerequisites(
                labels,
                preflight_prerequisite_labels,
                expected_sha=expected_live_sha,
            )
        )
    else:
        recovery_ok, recovery_detail = _run_restart_recovery_if_needed(labels)
    if not recovery_ok:
        print("REFUSING to restart — live restart recovery is not green:")
        print(recovery_detail)
        if includes_live_trading:
            restored, restore_detail = _restore_paused_live_monitoring_after_failed_restart(
                post_live_labels=post_live_labels,
                expected_sha=expected_live_sha,
            )
            print(restore_detail, file=sys.stderr)
            if not restored:
                print(
                    "held-capital monitoring restoration failed; entry pause remains armed",
                    file=sys.stderr,
                )
        return 1
    print(recovery_detail)

    if continuous_monitor_cutover:
        preflight_ok, preflight_detail = (
            True,
            "live restart preflight satisfied by warm full attestation and zero-main witness",
        )
    else:
        preflight_ok, preflight_detail = _run_restart_preflight_if_needed(labels)
    if not preflight_ok:
        print("REFUSING to restart — live restart preflight is not green:")
        print(preflight_detail)
        if includes_live_trading:
            restored, restore_detail = _restore_paused_live_monitoring_after_failed_restart(
                post_live_labels=post_live_labels,
                expected_sha=expected_live_sha,
            )
            print(restore_detail, file=sys.stderr)
            if not restored:
                print(
                    "held-capital monitoring restoration failed; entry pause remains armed",
                    file=sys.stderr,
                )
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
            # The venue-heartbeat watchdog already takes a shared lease on this
            # deploy's exclusive restart lock before it may repair launchd. Run
            # the sidecar as soon as process identity is known so CLOB liveness
            # can recover while monitor/queue proofs execute; the held lock
            # prevents it from restarting the process under verification.
            post_live_ok = True
            for label in post_live_labels:
                deferred_ok, deferred_detail = _launch_or_restart_label(label)
                if deferred_ok:
                    print(deferred_detail)
                else:
                    post_live_ok = False
                    rc_all = 1
                    print(deferred_detail, file=sys.stderr)
            queue_ok = False
            monitor_ok = False
            if not runtime_ok:
                rc_all = 1
            else:
                # Runtime restart recovery gives held-position monitoring priority.
                # Prove that obligation first; only then expect the reactor queue
                # to advance on the shared lane.
                monitor_ok, monitor_detail = _wait_for_post_start_monitor_cadence(
                    launched_after=launched_after,
                )
                print(monitor_detail)
                if not monitor_ok:
                    rc_all = 1
                else:
                    queue_ok, queue_detail = _wait_for_post_start_edli_queue_progress(
                        launched_after=launched_after,
                        post_start_freshness_verified=(runtime_ok and monitor_ok),
                    )
                    print(queue_detail)
                    if not queue_ok:
                        rc_all = 1
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
            _, restore_detail = _restore_paused_live_monitoring_after_failed_restart(
                post_live_labels=post_live_labels,
                expected_sha=expected_live_sha,
            )
            print(restore_detail, file=sys.stderr)
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
