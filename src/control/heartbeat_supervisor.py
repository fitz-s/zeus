"""Heartbeat supervision for live resting Polymarket orders.

R3 Z3: GTC/GTD placement is allowed only while the venue heartbeat is
healthy. Heartbeat loss is represented by the supervisor status; it does not
write or read the retired auto-pause tombstone.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import inspect
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from src.contracts.freshness_registry import FreshnessLevel, registry as _freshness_registry

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_CADENCE_SECONDS = 2
DEFAULT_HEARTBEAT_HTTP_TIMEOUT_SECONDS = 1.0
DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS = 8
DEFAULT_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS = 30
DEFAULT_HEARTBEAT_LEASE_RECOVERY_SUCCESS_TICKS = 3
HEARTBEAT_KEEPER_STATUS_FILENAME = "venue-heartbeat-keeper.json"
LIVE_TRADING_WATCHDOG_STATUS_FILENAME = "live-trading-launchd-watchdog.json"
LIVE_TRADING_LABEL = "com.zeus.live-trading"
LIVE_RESTART_LOCK_FILENAME = "deploy-live-restart.lock"
DEFAULT_LIVE_TRADING_WATCHDOG_CHECK_SECONDS = 60
DEFAULT_LIVE_TRADING_WATCHDOG_COOLDOWN_SECONDS = 300
_RESTING_ORDER_TYPES = {"GTC", "GTD"}
_IMMEDIATE_ORDER_TYPES = {"FOK", "FAK"}
_LIVE_TRADING_REQUIRED_SIDECAR_HEARTBEATS = (
    ("forecast-live", "forecast-live-heartbeat.json", 120.0),
    ("substrate-observer", "daemon-heartbeat-substrate-observer.json", 180.0),
    ("price-channel-ingest", "daemon-heartbeat-price-channel-ingest.json", 180.0),
    ("post-trade-capital", "daemon-heartbeat-post-trade-capital.json", 180.0),
)
_LIVE_TRADING_WATCHDOG_LAST_CHECK_MONOTONIC = 0.0
_LIVE_TRADING_WATCHDOG_LAST_ATTEMPT_MONOTONIC = 0.0
_HEARTBEAT_REQUEST_CAUSE_PRESERVED = False
_HEARTBEAT_TRANSPORT_RESET_COUNT = 0
_HEARTBEAT_LAST_TRANSPORT_RESET_REASON: str | None = None


class HeartbeatHealth(str, Enum):
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    LOST = "LOST"
    DISABLED_FOR_NON_RESTING_ONLY = "DISABLED_FOR_NON_RESTING_ONLY"


class OrderType(str, Enum):
    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class HeartbeatNotHealthy(RuntimeError):
    """Raised before live resting-order submit when heartbeat is not healthy."""


@dataclass(frozen=True)
class HeartbeatStatus:
    health: HeartbeatHealth
    last_success_at: Optional[datetime]
    consecutive_failures: int
    heartbeat_id: str
    cadence_seconds: int
    last_error: Optional[str] = None
    last_failure_at: Optional[datetime] = None
    last_invalid_id_at: Optional[datetime] = None
    consecutive_successes: int = 0
    lease_continuous_since: Optional[datetime] = None
    lease_gap_suspected_until: Optional[datetime] = None

    def resting_order_safe(self, *, now: Optional[datetime] = None) -> bool:
        if self.health is not HeartbeatHealth.HEALTHY:
            return False
        if self.lease_gap_suspected_until is None:
            return True
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        checked_at = checked_at.astimezone(timezone.utc)
        return checked_at >= self.lease_gap_suspected_until.astimezone(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "health": self.health.value,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "consecutive_failures": self.consecutive_failures,
            "heartbeat_id": self.heartbeat_id,
            "cadence_seconds": self.cadence_seconds,
            "last_error": self.last_error,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "last_invalid_id_at": self.last_invalid_id_at.isoformat() if self.last_invalid_id_at else None,
            "consecutive_successes": self.consecutive_successes,
            "lease_continuous_since": (
                self.lease_continuous_since.isoformat()
                if self.lease_continuous_since
                else None
            ),
            "lease_gap_suspected_until": (
                self.lease_gap_suspected_until.isoformat()
                if self.lease_gap_suspected_until
                else None
            ),
            "resting_order_safe": self.resting_order_safe(),
        }


def _normalize_order_type(order_type: str | OrderType | None) -> str:
    if isinstance(order_type, OrderType):
        return order_type.value
    if order_type is None:
        return "GTC"
    return str(order_type).upper()


def heartbeat_required_for(order_type: str | OrderType | None) -> bool:
    """Return whether an order type requires a healthy venue heartbeat.

    Unknown order types fail closed as heartbeat-required because Zeus must not
    accidentally treat a new resting type as immediate-only.
    """

    normalized = _normalize_order_type(order_type)
    if normalized in _IMMEDIATE_ORDER_TYPES:
        return False
    return True


def heartbeat_cadence_seconds_from_env() -> int:
    raw = os.environ.get("ZEUS_HEARTBEAT_CADENCE_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_HEARTBEAT_CADENCE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ZEUS_HEARTBEAT_CADENCE_SECONDS must be an integer") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_CADENCE_SECONDS must be positive")
    return value


def heartbeat_http_timeout_seconds_from_env(cadence_seconds: int) -> float:
    raw = os.environ.get("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS")
    if raw is None or raw == "":
        value = min(DEFAULT_HEARTBEAT_HTTP_TIMEOUT_SECONDS, float(cadence_seconds) / 2.0)
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be numeric") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be positive")
    max_timeout = float(cadence_seconds) / 2.0
    if value > max_timeout:
        raise ValueError(
            "ZEUS_HEARTBEAT_HTTP_TIMEOUT_SECONDS must be no longer than half "
            "the heartbeat cadence"
        )
    return value


def install_dedicated_heartbeat_http_timeout(*, cadence_seconds: int) -> bool:
    """Keep heartbeat HTTP blocking time below the lease cadence.

    The CLOB heartbeat rotates a server-owned lease token. A read timeout can
    leave the client unsure whether the server accepted and rotated the token;
    if the request blocks for a full cadence, one network stall can consume the
    whole lease window and Polymarket may cancel resting GTC/GTD orders. The
    keeper runs in its own process, so replacing the SDK module's global client
    here does not affect live evaluator/orderbook traffic.
    """

    global _HEARTBEAT_REQUEST_CAUSE_PRESERVED
    timeout_seconds = heartbeat_http_timeout_seconds_from_env(cadence_seconds)
    try:
        import httpx
        from py_clob_client_v2.exceptions import PolyApiException
        from py_clob_client_v2.http_helpers import helpers as heartbeat_http_helpers
    except Exception as exc:  # pragma: no cover - dependency absence is runtime-specific
        logger.warning("heartbeat HTTP timeout install skipped: %s", exc)
        return False

    old_client = getattr(heartbeat_http_helpers, "_http_client", None)
    heartbeat_http_helpers._http_client = httpx.Client(
        http2=False,
        timeout=httpx.Timeout(timeout_seconds),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )
    close = getattr(old_client, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.debug("old heartbeat HTTP client close failed", exc_info=True)

    if getattr(heartbeat_http_helpers, "_zeus_request_cause_preserved", False):
        _HEARTBEAT_REQUEST_CAUSE_PRESERVED = True
        return True

    def _request_with_cause(endpoint: str, method: str, headers=None, data=None, params=None):
        overloaded_headers = heartbeat_http_helpers._overload_headers(method, headers)
        overloaded_headers["Connection"] = "close"
        try:
            if isinstance(data, str):
                resp = heartbeat_http_helpers._http_client.request(
                    method=method,
                    url=endpoint,
                    headers=overloaded_headers,
                    content=data.encode("utf-8"),
                    params=params,
                )
            else:
                resp = heartbeat_http_helpers._http_client.request(
                    method=method,
                    url=endpoint,
                    headers=overloaded_headers,
                    json=data,
                    params=params,
                )

            if resp.status_code != 200:
                heartbeat_http_helpers.logger.error(
                    "[py_clob_client_v2] request error status=%s url=%s body=%s",
                    resp.status_code,
                    endpoint,
                    resp.text,
                )
                raise PolyApiException(resp)

            try:
                return resp.json()
            except ValueError:
                return resp.text
        except PolyApiException:
            raise
        except httpx.RequestError as exc:
            heartbeat_http_helpers.logger.error("[py_clob_client_v2] request error: %s", exc)
            raise PolyApiException(
                error_msg=f"Request exception: {type(exc).__name__}"
            ) from exc

    heartbeat_http_helpers.request = _request_with_cause
    heartbeat_http_helpers._zeus_request_cause_preserved = True
    _HEARTBEAT_REQUEST_CAUSE_PRESERVED = True
    return True


def _is_heartbeat_transport_error(exc: BaseException) -> bool:
    try:
        import httpx
    except Exception:  # pragma: no cover - dependency absence is runtime-specific
        return False

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.RequestError):
            return True
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        current = cause if isinstance(cause, BaseException) else context
        if not isinstance(current, BaseException):
            current = None
    return False


def _reset_dedicated_heartbeat_http_transport(
    *,
    cadence_seconds: int,
    cause: BaseException,
) -> None:
    global _HEARTBEAT_TRANSPORT_RESET_COUNT
    global _HEARTBEAT_LAST_TRANSPORT_RESET_REASON

    if not install_dedicated_heartbeat_http_timeout(cadence_seconds=cadence_seconds):
        raise RuntimeError("heartbeat HTTP transport reset unavailable")
    _HEARTBEAT_TRANSPORT_RESET_COUNT += 1
    _HEARTBEAT_LAST_TRANSPORT_RESET_REASON = _describe_heartbeat_exception(cause)
    logger.warning(
        "Venue heartbeat transport reset after %s",
        _HEARTBEAT_LAST_TRANSPORT_RESET_REASON,
    )


def heartbeat_transport_diagnostics() -> dict[str, Any]:
    return {
        "request_cause_preserved": bool(_HEARTBEAT_REQUEST_CAUSE_PRESERVED),
        "transport_reset_count": int(_HEARTBEAT_TRANSPORT_RESET_COUNT),
        "last_transport_reset_reason": _HEARTBEAT_LAST_TRANSPORT_RESET_REASON,
    }


def heartbeat_status_max_age_seconds_from_env() -> int:
    raw = os.environ.get("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS")
    if raw is None or raw == "":
        return DEFAULT_HEARTBEAT_STATUS_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS must be an integer") from exc
    if value <= 0:
        raise ValueError("ZEUS_HEARTBEAT_STATUS_MAX_AGE_SECONDS must be positive")
    return value


def heartbeat_keeper_status_path() -> Path:
    from src.config import state_path

    return state_path(HEARTBEAT_KEEPER_STATUS_FILENAME)


def live_trading_watchdog_status_path() -> Path:
    from src.config import state_path

    return state_path(LIVE_TRADING_WATCHDOG_STATUS_FILENAME)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def live_trading_launchd_watchdog_enabled() -> bool:
    """Return whether the venue-heartbeat sidecar should heal missing src.main.

    The watchdog is a narrow launchd-liveness bridge. It never submits, cancels,
    reconciles, or writes DB truth; it only bootstraps the active live-trading
    plist when launchd has no loaded service and all required sidecars already
    prove same-HEAD freshness.
    """

    default_enabled = str(os.environ.get("ZEUS_MODE") or "").lower() == "live"
    return _env_bool("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_ENABLED", default_enabled)


def _live_trading_watchdog_check_seconds() -> float:
    raw = os.environ.get("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_CHECK_SECONDS")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_LIVE_TRADING_WATCHDOG_CHECK_SECONDS
    except ValueError as exc:
        raise ValueError("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_CHECK_SECONDS must be numeric") from exc
    if value <= 0:
        raise ValueError("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_CHECK_SECONDS must be positive")
    return value


def _live_trading_watchdog_cooldown_seconds() -> float:
    raw = os.environ.get("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_COOLDOWN_SECONDS")
    try:
        value = float(raw) if raw not in (None, "") else DEFAULT_LIVE_TRADING_WATCHDOG_COOLDOWN_SECONDS
    except ValueError as exc:
        raise ValueError("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_COOLDOWN_SECONDS must be numeric") from exc
    if value < 0:
        raise ValueError("ZEUS_LIVE_TRADING_LAUNCHD_WATCHDOG_COOLDOWN_SECONDS must be non-negative")
    return value


def _live_trading_watchdog_write_status(
    payload: dict[str, Any],
    *,
    status_path: Path | None = None,
) -> dict[str, Any]:
    target = status_path or live_trading_watchdog_status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    enriched = {
        "schema_version": 1,
        "owner": "zeus-venue-heartbeat",
        "written_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(enriched, sort_keys=True) + "\n")
    tmp.replace(target)
    return enriched


def _launchd_gui_domain() -> str:
    return os.environ.get("ZEUS_GUI_DOMAIN") or f"gui/{os.getuid()}"


def _launchd_service_status(
    label: str,
    *,
    run_cmd: Any = subprocess.run,
) -> dict[str, Any]:
    try:
        res = run_cmd(
            ["launchctl", "print", f"{_launchd_gui_domain()}/{label}"],
            capture_output=True,
            text=True,
            timeout=8.0,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return {
            "loaded": False,
            "running": False,
            "detail": f"launchctl print unavailable: {exc}",
        }
    output = f"{getattr(res, 'stdout', '')}\n{getattr(res, 'stderr', '')}"
    status: dict[str, Any] = {
        "loaded": getattr(res, "returncode", 1) == 0,
        "running": False,
        "returncode": getattr(res, "returncode", None),
    }
    for line in output.splitlines():
        text = line.strip()
        if "=" not in text:
            continue
        key, value = (piece.strip() for piece in text.split("=", 1))
        if key == "state":
            status["state"] = value
        elif key == "pid":
            with contextlib.suppress(ValueError):
                status["pid"] = int(value)
        elif key == "active count":
            with contextlib.suppress(ValueError):
                status["active_count"] = int(value)
        elif key == "last exit code":
            with contextlib.suppress(ValueError):
                status["last_exit_status"] = int(value)
    state = str(status.get("state") or "").strip().lower()
    pid = status.get("pid")
    status["running"] = bool(status["loaded"] and (state == "running" or (isinstance(pid, int) and pid > 0)))
    return status


def _launchd_service_loaded(
    label: str,
    *,
    run_cmd: Any = subprocess.run,
) -> bool:
    return bool(_launchd_service_status(label, run_cmd=run_cmd).get("loaded"))


def _launchd_service_disabled(
    label: str,
    *,
    run_cmd: Any = subprocess.run,
) -> tuple[bool | None, str]:
    try:
        res = run_cmd(
            ["launchctl", "print-disabled", _launchd_gui_domain()],
            capture_output=True,
            text=True,
            timeout=8.0,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return None, f"launchctl print-disabled unavailable: {exc}"
    output = f"{getattr(res, 'stdout', '')}\n{getattr(res, 'stderr', '')}"
    needle = f'"{label}" => '
    for line in output.splitlines():
        if needle not in line:
            continue
        value = line.split("=>", 1)[1].strip().lower()
        if value.startswith("disabled"):
            return True, "disabled"
        if value.startswith("enabled"):
            return False, "enabled"
    if getattr(res, "returncode", 1) != 0:
        return None, f"launchctl print-disabled rc={getattr(res, 'returncode', '?')}"
    return False, "not_listed"


def _repo_root_from_config() -> Path:
    from src.config import PROJECT_ROOT

    return Path(PROJECT_ROOT)


def _state_root_from_config() -> Path:
    from src.config import STATE_DIR

    return Path(STATE_DIR)


@contextlib.contextmanager
def _live_restart_watchdog_lock(state_root: Path):
    """Hold shared bootstrap ownership unless deploy owns the restart lifecycle."""

    path = state_root / "locks" / LIVE_RESTART_LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _live_trading_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LIVE_TRADING_LABEL}.plist"


def _current_git_head(
    repo_root: Path,
    *,
    run_cmd: Any = subprocess.run,
) -> tuple[str | None, str | None]:
    try:
        res = run_cmd(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        return None, f"git rev-parse unavailable: {exc}"
    if getattr(res, "returncode", 1) != 0:
        detail = (getattr(res, "stderr", "") or getattr(res, "stdout", "") or "").strip()
        return None, f"git rev-parse rc={getattr(res, 'returncode', '?')}: {detail}"
    head = (getattr(res, "stdout", "") or "").strip()
    if not head:
        return None, "git rev-parse returned empty HEAD"
    return head, None


def _git_head_matches(expected: str, observed: str) -> bool:
    expected = str(expected or "").strip()
    observed = str(observed or "").strip()
    return bool(expected and observed and (expected == observed or expected.startswith(observed)))


def _live_trading_sidecars_ready(
    *,
    expected_sha: str | None,
    state_root: Path,
    now: datetime,
) -> tuple[bool, list[str], list[str]]:
    failures: list[str] = []
    identity_observations: list[str] = []
    for name, filename, max_age_seconds in _LIVE_TRADING_REQUIRED_SIDECAR_HEARTBEATS:
        path = state_root / filename
        try:
            payload = json.loads(path.read_text())
        except FileNotFoundError:
            failures.append(f"{name}:missing:{filename}")
            continue
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{name}:unreadable:{type(exc).__name__}")
            continue
        heartbeat_sha = str(payload.get("git_head") or "").strip()
        if expected_sha and not _git_head_matches(expected_sha, heartbeat_sha):
            identity_observations.append(
                f"{name}:git_head_mismatch heartbeat={heartbeat_sha or '<missing>'} "
                f"expected={expected_sha[:8]}"
            )
        heartbeat_at = _parse_utc(
            payload.get("alive_at") or payload.get("written_at") or payload.get("timestamp")
        )
        if heartbeat_at is None:
            failures.append(f"{name}:timestamp_invalid")
            continue
        age_seconds = (now - heartbeat_at).total_seconds()
        if age_seconds < -5.0 or age_seconds > max_age_seconds:
            failures.append(
                f"{name}:stale age_seconds={age_seconds:.1f} max={max_age_seconds:.1f}"
            )
    return not failures, failures, identity_observations


def recover_missing_live_trading_launchd_if_needed(
    *,
    now: datetime | None = None,
    run_cmd: Any = subprocess.run,
    repo_root: Path | None = None,
    state_root: Path | None = None,
    plist_path: Path | None = None,
    status_path: Path | None = None,
) -> dict[str, Any]:
    """Bootstrap live-trading only outside an operator-owned deploy restart."""

    resolved_state_root = state_root or _state_root_from_config()
    if not live_trading_launchd_watchdog_enabled():
        return _recover_missing_live_trading_launchd_under_restart_lock(
            now=now,
            run_cmd=run_cmd,
            repo_root=repo_root,
            state_root=resolved_state_root,
            plist_path=plist_path,
            status_path=status_path,
        )
    try:
        with _live_restart_watchdog_lock(resolved_state_root) as acquired:
            if not acquired:
                return _live_trading_watchdog_write_status(
                    {
                        "ok": True,
                        "action": "none",
                        "reason": "deploy_restart_in_progress",
                    },
                    status_path=status_path,
                )
            return _recover_missing_live_trading_launchd_under_restart_lock(
                now=now,
                run_cmd=run_cmd,
                repo_root=repo_root,
                state_root=resolved_state_root,
                plist_path=plist_path,
                status_path=status_path,
            )
    except OSError as exc:
        return _live_trading_watchdog_write_status(
            {
                "ok": False,
                "action": "blocked",
                "reason": "restart_lock_unavailable",
                "detail": f"{type(exc).__name__}: {exc}",
            },
            status_path=status_path,
        )


def _recover_missing_live_trading_launchd_under_restart_lock(
    *,
    now: datetime | None = None,
    run_cmd: Any = subprocess.run,
    repo_root: Path | None = None,
    state_root: Path | None = None,
    plist_path: Path | None = None,
    status_path: Path | None = None,
) -> dict[str, Any]:
    """Bootstrap missing live-trading only when its liveness prerequisites are proven.

    This is intentionally narrower than ``scripts/deploy_live.py restart`` so it
    can run inside the venue-heartbeat sidecar without restarting that sidecar
    from underneath itself.
    """

    checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not live_trading_launchd_watchdog_enabled():
        return _live_trading_watchdog_write_status(
            {"ok": True, "action": "none", "reason": "watchdog_disabled"},
            status_path=status_path,
        )
    launchd_status = _launchd_service_status(LIVE_TRADING_LABEL, run_cmd=run_cmd)
    if launchd_status.get("loaded"):
        if launchd_status.get("running"):
            return _live_trading_watchdog_write_status(
                {
                    "ok": True,
                    "action": "none",
                    "reason": "service_running",
                    "launchd_state": launchd_status.get("state"),
                    "pid": launchd_status.get("pid"),
                    "active_count": launchd_status.get("active_count"),
                },
                status_path=status_path,
            )
        return _live_trading_watchdog_write_status(
            {
                "ok": False,
                "action": "blocked",
                "reason": "service_loaded_not_running",
                "launchd_state": launchd_status.get("state"),
                "pid": launchd_status.get("pid"),
                "active_count": launchd_status.get("active_count"),
                "last_exit_status": launchd_status.get("last_exit_status"),
            },
            status_path=status_path,
        )

    disabled, disabled_detail = _launchd_service_disabled(LIVE_TRADING_LABEL, run_cmd=run_cmd)
    if disabled is True:
        return _live_trading_watchdog_write_status(
            {"ok": False, "action": "blocked", "reason": "service_disabled"},
            status_path=status_path,
        )
    if disabled is None:
        return _live_trading_watchdog_write_status(
            {
                "ok": False,
                "action": "blocked",
                "reason": "disabled_state_unproven",
                "detail": disabled_detail,
            },
            status_path=status_path,
        )

    active_plist = plist_path or _live_trading_plist_path()
    if not active_plist.exists():
        return _live_trading_watchdog_write_status(
            {
                "ok": False,
                "action": "blocked",
                "reason": "active_plist_missing",
                "plist": str(active_plist),
            },
            status_path=status_path,
        )

    root = repo_root or _repo_root_from_config()
    expected_sha, git_error = _current_git_head(root, run_cmd=run_cmd)
    sidecars_ok, sidecar_failures, identity_observations = _live_trading_sidecars_ready(
        expected_sha=expected_sha,
        state_root=state_root or _state_root_from_config(),
        now=checked_at,
    )
    if not sidecars_ok:
        return _live_trading_watchdog_write_status(
            {
                "ok": False,
                "action": "blocked",
                "reason": "sidecars_not_ready",
                "expected_sha": expected_sha,
                "failures": sidecar_failures,
                "identity_observations": identity_observations,
            },
            status_path=status_path,
        )

    res = run_cmd(
        ["launchctl", "bootstrap", _launchd_gui_domain(), str(active_plist)],
        capture_output=True,
        text=True,
        timeout=20.0,
    )
    output = " ".join(
        piece.strip()
        for piece in (getattr(res, "stdout", ""), getattr(res, "stderr", ""))
        if piece and piece.strip()
    )
    if getattr(res, "returncode", 1) == 0:
        return _live_trading_watchdog_write_status(
            {
                "ok": True,
                "action": "bootstrapped",
                "reason": "service_missing",
                "expected_sha": expected_sha,
                "identity_observations": identity_observations,
                "git_identity_error": git_error,
                "plist": str(active_plist),
            },
            status_path=status_path,
        )
    if "already" in output.lower() and "service" in output.lower():
        return _live_trading_watchdog_write_status(
            {
                "ok": True,
                "action": "none",
                "reason": "already_loaded_race",
                "expected_sha": expected_sha,
                "identity_observations": identity_observations,
                "git_identity_error": git_error,
            },
            status_path=status_path,
        )
    return _live_trading_watchdog_write_status(
        {
            "ok": False,
            "action": "bootstrap_failed",
            "reason": "launchctl_bootstrap_failed",
            "expected_sha": expected_sha,
            "identity_observations": identity_observations,
            "git_identity_error": git_error,
            "returncode": getattr(res, "returncode", None),
            "detail": output,
        },
        status_path=status_path,
    )


def maybe_recover_missing_live_trading_launchd(
    *,
    now: datetime | None = None,
    run_cmd: Any = subprocess.run,
    status_path: Path | None = None,
) -> dict[str, Any] | None:
    global _LIVE_TRADING_WATCHDOG_LAST_ATTEMPT_MONOTONIC
    global _LIVE_TRADING_WATCHDOG_LAST_CHECK_MONOTONIC

    monotonic_now = time.monotonic()
    check_seconds = _live_trading_watchdog_check_seconds()
    if monotonic_now - _LIVE_TRADING_WATCHDOG_LAST_CHECK_MONOTONIC < check_seconds:
        return None
    _LIVE_TRADING_WATCHDOG_LAST_CHECK_MONOTONIC = monotonic_now

    if (
        _LIVE_TRADING_WATCHDOG_LAST_ATTEMPT_MONOTONIC
        and monotonic_now - _LIVE_TRADING_WATCHDOG_LAST_ATTEMPT_MONOTONIC
        < _live_trading_watchdog_cooldown_seconds()
        and not _launchd_service_loaded(LIVE_TRADING_LABEL, run_cmd=run_cmd)
    ):
        return _live_trading_watchdog_write_status(
            {"ok": False, "action": "blocked", "reason": "cooldown"},
            status_path=status_path,
        )

    result = recover_missing_live_trading_launchd_if_needed(
        now=now,
        run_cmd=run_cmd,
        status_path=status_path,
    )
    if result.get("action") in {"bootstrapped", "bootstrap_failed"}:
        _LIVE_TRADING_WATCHDOG_LAST_ATTEMPT_MONOTONIC = monotonic_now
    return result


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_invalid_heartbeat_id_error(exc: Exception | str) -> bool:
    return "Invalid Heartbeat ID" in str(exc)


def _describe_heartbeat_exception(exc: Exception | str) -> str:
    """Return a sanitized, cause-aware heartbeat error for operator status.

    The py-clob SDK can collapse transport failures to the opaque string
    ``PolyApiException[status_code=None, error_message=Request exception!]``.
    That is fail-closed enough for safety, but not diagnostic enough to repair
    live heartbeat loss. Include exception class and immediate cause/context
    class/message while keeping the payload compact and free of credentials.
    """

    if not isinstance(exc, BaseException):
        return str(exc)

    def _clean(value: object) -> str:
        text = str(value)
        for marker in (
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_API_PASSPHRASE",
            "private_key",
            "api_secret",
            "api_key",
            "passphrase",
        ):
            text = text.replace(marker, "[redacted-field]")
        return " ".join(text.split())

    pieces = [f"{type(exc).__name__}: {_clean(exc)}"]
    cause = getattr(exc, "__cause__", None)
    context = getattr(exc, "__context__", None)
    if cause is not None:
        pieces.append(f"cause={type(cause).__name__}: {_clean(cause)}")
    elif context is not None:
        pieces.append(f"context={type(context).__name__}: {_clean(context)}")
    return "; ".join(piece for piece in pieces if piece and not piece.endswith(": "))


def write_heartbeat_keeper_status(
    status: HeartbeatStatus,
    *,
    path: Path | None = None,
    owner: str = "zeus-venue-heartbeat",
) -> Path:
    target = path or heartbeat_keeper_status_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": 2,
        "owner": owner,
        "written_at": now.isoformat(),
        "transport_diagnostics": heartbeat_transport_diagnostics(),
        **status.to_dict(),
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n")
    tmp.replace(target)
    return target


def fresh_heartbeat_id_from_status(
    *,
    path: Path | None = None,
    max_age_seconds: int = DEFAULT_HEARTBEAT_RESTART_SEED_MAX_AGE_SECONDS,
) -> str:
    """Return the latest fresh venue heartbeat chain token for restart handoff.

    The CLOB heartbeat id is a server-rotated lease token. A daemon restart must
    continue the last fresh token instead of registering a new empty chain while
    the old server-side lease is still active.
    """

    target = path or heartbeat_keeper_status_path()
    try:
        payload = json.loads(target.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    written_at = _parse_utc(payload.get("written_at"))
    if written_at is None:
        return ""
    age_seconds = (datetime.now(timezone.utc) - written_at).total_seconds()
    if age_seconds < 0 or _freshness_registry.evaluate("heartbeat_restart_seed", age_seconds, override_threshold_seconds=max_age_seconds) >= FreshnessLevel.STALE:
        return ""
    if str(payload.get("health") or "").upper() != HeartbeatHealth.HEALTHY.value:
        return ""
    heartbeat_id = str(payload.get("heartbeat_id") or "").strip()
    return heartbeat_id


class ExternalHeartbeatSupervisor:
    """Read-only gate over a daemon-independent heartbeat keeper status file."""

    def __init__(
        self,
        *,
        status_path: Path | None = None,
        max_age_seconds: int | None = None,
        cadence_seconds: int | None = None,
    ) -> None:
        self._status_path = status_path or heartbeat_keeper_status_path()
        self._max_age_seconds = (
            heartbeat_status_max_age_seconds_from_env()
            if max_age_seconds is None
            else int(max_age_seconds)
        )
        self._cadence_seconds = (
            heartbeat_cadence_seconds_from_env()
            if cadence_seconds is None
            else int(cadence_seconds)
        )

    def status(self) -> HeartbeatStatus:
        try:
            payload = json.loads(self._status_path.read_text())
        except FileNotFoundError:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=None,
                consecutive_failures=0,
                heartbeat_id="external",
                cadence_seconds=self._cadence_seconds,
                last_error=f"external heartbeat status missing: {self._status_path}",
            )
        except (OSError, json.JSONDecodeError) as exc:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=None,
                consecutive_failures=0,
                heartbeat_id="external",
                cadence_seconds=self._cadence_seconds,
                last_error=f"external heartbeat status unreadable: {exc}",
            )

        written_at = _parse_utc(payload.get("written_at"))
        last_success_at = _parse_utc(payload.get("last_success_at"))
        last_failure_at = _parse_utc(payload.get("last_failure_at"))
        last_invalid_id_at = _parse_utc(payload.get("last_invalid_id_at"))
        lease_continuous_since = _parse_utc(payload.get("lease_continuous_since"))
        lease_gap_suspected_until = _parse_utc(payload.get("lease_gap_suspected_until"))
        consecutive_successes = int(payload.get("consecutive_successes") or 0)

        if written_at is None:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=last_success_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                heartbeat_id="external",
                cadence_seconds=int(payload.get("cadence_seconds") or self._cadence_seconds),
                last_error="external heartbeat status missing written_at",
                last_failure_at=last_failure_at,
                last_invalid_id_at=last_invalid_id_at,
                consecutive_successes=consecutive_successes,
                lease_continuous_since=lease_continuous_since,
                lease_gap_suspected_until=lease_gap_suspected_until,
            )
        age_seconds = (datetime.now(timezone.utc) - written_at).total_seconds()
        cadence_seconds = int(payload.get("cadence_seconds") or self._cadence_seconds)
        if _freshness_registry.evaluate("heartbeat_status", age_seconds, override_threshold_seconds=self._max_age_seconds) >= FreshnessLevel.STALE:
            return HeartbeatStatus(
                health=HeartbeatHealth.LOST,
                last_success_at=last_success_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                heartbeat_id="external",
                cadence_seconds=cadence_seconds,
                last_error=(
                    f"external heartbeat status stale: age={age_seconds:.3f}s "
                    f"max_age={self._max_age_seconds}s"
                ),
                last_failure_at=last_failure_at,
                last_invalid_id_at=last_invalid_id_at,
                consecutive_successes=consecutive_successes,
                lease_continuous_since=lease_continuous_since,
                lease_gap_suspected_until=lease_gap_suspected_until,
            )
        raw_health = str(payload.get("health") or "").upper()
        try:
            health = HeartbeatHealth(raw_health)
        except ValueError:
            health = HeartbeatHealth.LOST
        return HeartbeatStatus(
            health=health,
            last_success_at=last_success_at,
            consecutive_failures=int(payload.get("consecutive_failures") or 0),
            heartbeat_id=str(payload.get("heartbeat_id") or "external"),
            cadence_seconds=cadence_seconds,
            last_error=payload.get("last_error"),
            last_failure_at=last_failure_at,
            last_invalid_id_at=last_invalid_id_at,
            consecutive_successes=consecutive_successes,
            lease_continuous_since=lease_continuous_since,
            lease_gap_suspected_until=lease_gap_suspected_until,
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        return self.status().resting_order_safe()


class HeartbeatSupervisor:
    def __init__(
        self,
        adapter: Any,
        cadence_seconds: int = DEFAULT_HEARTBEAT_CADENCE_SECONDS,
        *,
        initial_heartbeat_id: str = "",
        transport_reset: Callable[[BaseException], None] | None = None,
    ) -> None:
        if cadence_seconds <= 0:
            raise ValueError("cadence_seconds must be positive")
        self._adapter = adapter
        self._cadence_seconds = int(cadence_seconds)
        # Polymarket CLOB v2 heartbeat protocol: client must NOT mint its own ID.
        # First post sends the empty string; the server creates a session and
        # returns its assigned `heartbeat_id`. Each subsequent successful post
        # rotates the canonical ID to a new value, which the client must echo
        # on the next tick. Probed against clob.polymarket.com 2026-05-01:
        #     POST {heartbeat_id:""}              -> {heartbeat_id:"A"}
        #     POST {heartbeat_id:"A"}             -> {heartbeat_id:"B"}    # rotates
        #     POST {heartbeat_id:"<bogus>"}       -> 400 Invalid Heartbeat ID
        # On any 4xx we restart the chain with "" rather than minting a UUID
        # (the previous bug — a fresh UUID never matches the server record).
        self._heartbeat_id: str = str(initial_heartbeat_id or "")
        self._health = HeartbeatHealth.STARTING
        self._last_success_at: Optional[datetime] = None
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_error: Optional[str] = None
        self._last_failure_at: Optional[datetime] = None
        self._last_invalid_id_at: Optional[datetime] = None
        self._lease_continuous_since: Optional[datetime] = None
        self._lease_gap_suspected_until: Optional[datetime] = None
        self._transport_reset = transport_reset
        self._running = False
        self._run_once_lock = threading.Lock()

    async def start(self) -> None:
        """Run heartbeat posts until stop() is called.

        The daemon currently schedules run_once() through APScheduler; start()
        exists for async runtimes and tests that want a continuous coroutine.
        """

        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self._cadence_seconds)

    async def stop(self) -> None:
        self._running = False

    async def run_once(self) -> HeartbeatStatus:
        """Post one heartbeat and update health state.

        Implements the Polymarket chain-token heartbeat protocol — see the
        comment in __init__. Each successful post returns the next canonical
        `heartbeat_id` which we capture for the following tick. Transient
        failures keep that id so existing resting orders stay tied to the same
        lease chain; explicit Invalid Heartbeat ID alone restarts from `""`.
        A generic network failure is ambiguous: the venue might have processed
        the POST without returning the rotated token. Sending an empty-chain
        POST in that same tick can therefore create a second lease chain and
        amplify an upstream outage. It records the failure, resets only the
        dedicated transport when appropriate, and retries the preserved token
        on the next tick.
        """

        if not self._run_once_lock.acquire(blocking=False):
            logger.warning("Venue heartbeat tick skipped: previous tick still in flight")
            return self.status()
        try:
            try:
                self._heartbeat_id = await self._post_heartbeat_once(self._heartbeat_id)
                self.record_success()
            except Exception as exc:  # fail closed, surface through status
                # Polymarket echoes the rejected heartbeat_id in the 400 body.
                # Treating that value as a fresh hint can pin the lease owner to
                # a known-bad token until a later timeout resets the chain, which
                # is long enough for the venue to cancel resting GTC/GTD orders.
                if _is_invalid_heartbeat_id_error(exc):
                    self.record_failure(exc)
                    try:
                        self._heartbeat_id = await self._post_heartbeat_once("")
                        self.record_success()
                        return self.status()
                    except Exception as retry_exc:
                        self._reset_transport_after_failure(retry_exc)
                        exc = RuntimeError(
                            f"Invalid Heartbeat ID; empty-chain recovery failed: {retry_exc}"
                        )
                    self._heartbeat_id = ""  # invalid chain cannot protect resting orders
                    self.record_failure(exc)
                else:
                    self.record_failure(exc)
                    self._reset_transport_after_failure(exc)
        finally:
            self._run_once_lock.release()
        return self.status()

    def _reset_transport_after_failure(self, exc: BaseException) -> None:
        if self._transport_reset is None or not _is_heartbeat_transport_error(exc):
            return
        try:
            self._transport_reset(exc)
        except Exception as reset_exc:  # fail closed; next tick may retry the reset.
            logger.warning("Venue heartbeat transport reset failed: %s", reset_exc)

    async def _post_heartbeat_once(self, heartbeat_id: str) -> str:
        if self._adapter is None:
            raise RuntimeError("heartbeat adapter unavailable")
        ack = self._adapter.post_heartbeat(heartbeat_id)
        if inspect.isawaitable(ack):
            ack = await ack
        if getattr(ack, "ok", True) is False:
            raise RuntimeError("heartbeat ack returned ok=False")
        raw = getattr(ack, "raw", None)
        next_id = ""
        if isinstance(raw, dict):
            next_id = str(raw.get("heartbeat_id") or "")
        if not next_id:
            raise RuntimeError("heartbeat ack missing heartbeat_id")
        return next_id

    def record_success(self) -> None:
        now = datetime.now(timezone.utc)
        if self._consecutive_successes == 0:
            self._lease_continuous_since = now
        self._health = HeartbeatHealth.HEALTHY
        self._last_success_at = now
        self._consecutive_failures = 0
        self._consecutive_successes += 1
        self._last_error = None

    def record_failure(self, exc: Exception | str) -> None:
        now = datetime.now(timezone.utc)
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        self._last_error = _describe_heartbeat_exception(exc)
        self._last_failure_at = now
        self._lease_continuous_since = None
        self._lease_gap_suspected_until = now + timedelta(
            seconds=self._cadence_seconds * DEFAULT_HEARTBEAT_LEASE_RECOVERY_SUCCESS_TICKS
        )
        if _is_invalid_heartbeat_id_error(exc):
            self._last_invalid_id_at = now
            self._health = HeartbeatHealth.LOST
        elif self._consecutive_failures == 1:
            self._health = HeartbeatHealth.DEGRADED
        else:
            self._health = HeartbeatHealth.LOST
        logger.warning(
            "Venue heartbeat failure (%s): health=%s error=%s",
            self._consecutive_failures,
            self._health.value,
            self._last_error,
        )

    def status(self) -> HeartbeatStatus:
        return HeartbeatStatus(
            health=self._health,
            last_success_at=self._last_success_at,
            consecutive_failures=self._consecutive_failures,
            heartbeat_id=self._heartbeat_id,
            cadence_seconds=self._cadence_seconds,
            last_error=self._last_error,
            last_failure_at=self._last_failure_at,
            last_invalid_id_at=self._last_invalid_id_at,
            consecutive_successes=self._consecutive_successes,
            lease_continuous_since=self._lease_continuous_since,
            lease_gap_suspected_until=self._lease_gap_suspected_until,
        )

    def gate_for_order_type(self, order_type: str | OrderType | None) -> bool:
        if not heartbeat_required_for(order_type):
            return True
        return self.status().resting_order_safe()


_GLOBAL_SUPERVISOR: Optional[Any] = None


def configure_global_supervisor(supervisor: Optional[Any]) -> None:
    global _GLOBAL_SUPERVISOR
    _GLOBAL_SUPERVISOR = supervisor


def get_global_supervisor() -> Optional[Any]:
    return _GLOBAL_SUPERVISOR


def current_status() -> HeartbeatStatus:
    supervisor = get_global_supervisor()
    if supervisor is None:
        return HeartbeatStatus(
            health=HeartbeatHealth.LOST,
            last_success_at=None,
            consecutive_failures=0,
            heartbeat_id="unconfigured",
            cadence_seconds=heartbeat_cadence_seconds_from_env(),
            last_error="heartbeat supervisor not configured",
        )
    return supervisor.status()


def assert_heartbeat_allows_order_type(order_type: str | OrderType | None = OrderType.GTC) -> None:
    normalized = _normalize_order_type(order_type)
    if not heartbeat_required_for(normalized):
        return
    supervisor = get_global_supervisor()
    status = current_status()
    allowed = supervisor.gate_for_order_type(normalized) if supervisor is not None else False
    if not allowed:
        raise HeartbeatNotHealthy(f"heartbeat={status.health.value}; order_type={normalized}; {status.last_error or ''}")


def summary() -> dict[str, Any]:
    status = current_status()
    supervisor = get_global_supervisor()
    resting_allowed = (
        supervisor.gate_for_order_type(OrderType.GTC)
        if supervisor is not None
        else False
    )
    allowed_order_types = [OrderType.FOK.value, OrderType.FAK.value]
    if resting_allowed:
        allowed_order_types = [order_type.value for order_type in OrderType]
    payload = status.to_dict()
    payload["entry"] = {
        "allow_submit": bool(allowed_order_types),
        "allowed_order_types": allowed_order_types,
        "resting_allow_submit": resting_allowed,
        "immediate_allow_submit": True,
        "required_order_types": sorted(_RESTING_ORDER_TYPES),
    }
    if not resting_allowed:
        payload["entry"]["restriction_reason"] = status.last_error or (
            "heartbeat_lease_gap_suspected"
            if status.health is HeartbeatHealth.HEALTHY
            else f"heartbeat={status.health.value}"
        )
    return payload


def data_lane_health_check(
    *,
    lane_write_failures: dict | None,
    decision_lane_writes: dict | None,
    expected_lanes: "set[str] | list[str] | tuple[str, ...] | None" = None,
    emit: bool = True,
) -> dict[str, Any]:
    """Lane-liveness health check: a dead decision/telemetry lane fails loud.

    AB3 (2026-06-16, timing-semantics fix). BASIS: a swallowed lane-write
    exception is INDISTINGUISHABLE from no-activity — a decision lane
    (edli_no_submit_receipts) sat dead from 2026-06-06 and nobody noticed
    because lane-write failures were swallowed silently and a dead lane looks
    identical to a quiet one. ``cycle_runtime._record_lane_write_failure`` /
    ``_record_lane_write_success`` now name + count each lane on the per-cycle
    summary; this surfaces them on the heartbeat.

    Reads the per-cycle ``lane_write_failures`` (lane -> failure count) and
    ``decision_lane_writes`` (lane -> success count) maps and flags:
      - any lane with a NONZERO failure count, and
      - any lane in ``expected_lanes`` with ZERO writes this window
        (only checked when ``expected_lanes`` is supplied — without an explicit
        expectation a quiet lane is not assumed dead).

    OBSERVABILITY ONLY: emits a ``logger.warning`` naming each flagged lane and
    returns a structured verdict. It MUST NOT gate or block trading (operator
    law: no caps / no artificial throttles). Pure aside from the warning log;
    safe to call every heartbeat/cycle.
    """
    failures = lane_write_failures if isinstance(lane_write_failures, dict) else {}
    writes = decision_lane_writes if isinstance(decision_lane_writes, dict) else {}

    failed_lanes = {
        str(lane): int(count or 0)
        for lane, count in failures.items()
        if int(count or 0) > 0
    }

    zero_write_lanes: list[str] = []
    if expected_lanes:
        for lane in expected_lanes:
            lane_name = str(lane)
            wrote = int(writes.get(lane_name, 0) or 0)
            # A lane that failed is already flagged above; only call out an
            # EXPECTED lane that produced neither a success nor a failure.
            if wrote == 0 and lane_name not in failed_lanes:
                zero_write_lanes.append(lane_name)

    healthy = not failed_lanes and not zero_write_lanes
    verdict: dict[str, Any] = {
        "ok": healthy,
        "failed_lanes": failed_lanes,
        "zero_write_lanes": sorted(zero_write_lanes),
        "scope": "cycle_pulse",
    }

    if emit and not healthy:
        if failed_lanes:
            logger.warning(
                "DATA LANE UNHEALTHY: %d lane(s) had write FAILURES this cycle: %s",
                len(failed_lanes),
                failed_lanes,
            )
        if zero_write_lanes:
            logger.warning(
                "DATA LANE UNHEALTHY: %d expected lane(s) wrote ZERO times this "
                "cycle (possible dead lane): %s",
                len(zero_write_lanes),
                sorted(zero_write_lanes),
            )

    return verdict


async def run_global_heartbeat_once() -> HeartbeatStatus:
    supervisor = get_global_supervisor()
    if supervisor is None:
        raise HeartbeatNotHealthy("heartbeat supervisor not configured")
    if not hasattr(supervisor, "run_once"):
        raise HeartbeatNotHealthy("heartbeat supervisor is external read-only")
    return await supervisor.run_once()


def run_heartbeat_keeper(
    *,
    adapter: Any | None = None,
    status_path: Path | None = None,
    cadence_seconds: int | None = None,
    max_ticks: int | None = None,
    sleep_fn: Any = time.sleep,
) -> None:
    """Run the minimal CLOB heartbeat lease owner.

    This loop owns only the venue heartbeat and its local status file. It does
    not read orders, reconcile, cancel, refresh collateral, or write DB truth.
    """

    cadence = heartbeat_cadence_seconds_from_env() if cadence_seconds is None else int(cadence_seconds)
    owns_transport = adapter is None
    if owns_transport:
        install_dedicated_heartbeat_http_timeout(cadence_seconds=cadence)
        from src.data.proxy_health import bypass_dead_proxy_env_vars

        bypass_dead_proxy_env_vars()
        from src.data.polymarket_client import PolymarketClient

        adapter = PolymarketClient()._ensure_v2_adapter()
    initial_heartbeat_id = fresh_heartbeat_id_from_status(path=status_path)
    supervisor = HeartbeatSupervisor(
        adapter,
        cadence_seconds=cadence,
        initial_heartbeat_id=initial_heartbeat_id,
        transport_reset=(
            lambda exc: _reset_dedicated_heartbeat_http_transport(
                cadence_seconds=cadence,
                cause=exc,
            )
        )
        if owns_transport
        else None,
    )
    ticks = 0
    while True:
        started = datetime.now(timezone.utc)
        status = asyncio.run(supervisor.run_once())
        write_heartbeat_keeper_status(status, path=status_path)
        try:
            maybe_recover_missing_live_trading_launchd(now=started)
        except Exception as exc:  # noqa: BLE001 - watchdog must never stop heartbeat lease.
            logger.warning("live-trading launchd watchdog failed: %s", exc, exc_info=True)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            return
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        sleep_fn(max(0.1, cadence - elapsed))


def _main(argv: list[str] | None = None) -> int:
    _start = time.monotonic()  # F86: process start time for SIGTERM elapsed log
    # F86: forensic SIGTERM trail — logs elapsed seconds to stderr before exit.
    _log = logging.getLogger(__name__)
    signal.signal(
        signal.SIGTERM,
        lambda s, f: (
            _log.error(
                "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
                os.getpid(), os.getppid(), int(time.monotonic() - _start),
            ),
            sys.exit(0),
        ),
    )
    parser = argparse.ArgumentParser(description="Run the Zeus CLOB venue heartbeat keeper.")
    parser.add_argument("--once", action="store_true", help="post one heartbeat and exit")
    parser.add_argument("--status-path", help="override status JSON path")
    args = parser.parse_args(argv)
    status_path = Path(args.status_path).expanduser() if args.status_path else None
    run_heartbeat_keeper(status_path=status_path, max_ticks=1 if args.once else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
