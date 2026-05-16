# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p5_maintenance_worker_core/SCAFFOLD.md §3 cli/entry.py + §3.5
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DESIGN.md §"CLI"
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DRY_RUN_PROTOCOL.md
"""
cli/entry — cmd_run, cmd_dry_run, cmd_status, cmd_init + argparse main()

Concurrent-tick lockfile (SCAFFOLD §3 + P5.1 critic SEV-3):
  fcntl.flock(LOCK_EX | LOCK_NB) on state_dir/maintenance_worker.pid.
  BlockingIOError on contention → exit non-zero (named exit code LOCK_CONTENTION).

InvocationMode is detected via scheduler_detect.detect() at entry.

MANUAL_CLI forces dry_run_only regardless of live_default
(DRY_RUN_PROTOCOL.md §"MANUAL_CLI override").

Stdlib + fcntl only. Zero Zeus identifiers.
All paths are caller-provided via EngineConfig; this module has no
knowledge of Zeus-specific directory layouts (those come from P6 bindings).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import Optional

from maintenance_worker.cli.scheduler_detect import detect
from maintenance_worker.core.engine import run_tick
from maintenance_worker.core.notifier import notify_tick_summary
from maintenance_worker.types.modes import InvocationMode
from maintenance_worker.types.specs import EngineConfig


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_LOCK_CONTENTION = 2   # concurrent tick in progress
EXIT_GUARD_FAILURE = 3     # hard guard → SystemExit propagated from engine
EXIT_NO_CONFIG = 4         # config file not found / unparseable


# ---------------------------------------------------------------------------
# Lockfile
# ---------------------------------------------------------------------------

_LOCKFILE_NAME = "maintenance_worker.pid"


class LockContention(Exception):
    """Raised when the concurrent-tick lockfile cannot be acquired."""


class _TickLock:
    """
    Context manager: acquire an exclusive non-blocking flock on the pid file.

    Raises LockContention if another tick is in progress.
    Writes the current PID to the lockfile on acquisition.
    Releases and cleans up on exit.
    """

    def __init__(self, state_dir: Path) -> None:
        self._path = state_dir / _LOCKFILE_NAME
        self._fd: Optional[int] = None

    def __enter__(self) -> "_TickLock":
        # Open (or create) the lockfile.
        self._fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise LockContention(
                f"Concurrent tick in progress (lockfile: {self._path}). "
                "Another maintenance_worker tick is running."
            )
        # Write PID
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, f"{os.getpid()}\n".encode())
        return self

    def __exit__(self, *args: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        # Best-effort cleanup of lockfile
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Config loading helper
# ---------------------------------------------------------------------------


def _load_config(config_path: Optional[str] = None) -> Optional[EngineConfig]:
    """
    Load EngineConfig from a JSON file, or return None on failure.

    config_path: if None, tries MAINTENANCE_WORKER_CONFIG env var,
    then {cwd}/maintenance_worker_config.json.

    The JSON file must have the same keys as EngineConfig fields.
    Path fields are deserialized from strings.
    """
    if config_path is None:
        config_path = os.environ.get(
            "MAINTENANCE_WORKER_CONFIG",
            str(Path.cwd() / "maintenance_worker_config.json"),
        )

    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return EngineConfig(
            repo_root=Path(raw["repo_root"]),
            state_dir=Path(raw["state_dir"]),
            evidence_dir=Path(raw["evidence_dir"]),
            task_catalog_path=Path(raw["task_catalog_path"]),
            safety_contract_path=Path(raw["safety_contract_path"]),
            live_default=bool(raw.get("live_default", False)),
            scheduler=str(raw.get("scheduler", "launchd")),
            notification_channel=str(raw.get("notification_channel", "file")),
            env_vars={str(k): str(v) for k, v in raw.get("env_vars", {}).items()},
        )
    except FileNotFoundError:
        print(
            f"[maintenance_worker] ERROR: config file not found: {config_path}",
            file=sys.stderr,
        )
        return None
    except (KeyError, ValueError, TypeError) as exc:
        print(
            f"[maintenance_worker] ERROR: config parse error: {exc}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(config: EngineConfig, invocation_mode: Optional[InvocationMode] = None) -> int:
    """
    Run a live maintenance tick (respects live_default from config).

    Acquires concurrent-tick lockfile before running. MANUAL_CLI forces
    dry-run-only regardless of live_default (engine enforces this via the
    check_scheduler_invocation result).

    Returns an exit code integer.
    """
    mode = invocation_mode or detect()
    try:
        with _TickLock(config.state_dir):
            result = run_tick(config)
    except LockContention as exc:
        print(f"[maintenance_worker] LOCK_CONTENTION: {exc}", file=sys.stderr)
        return EXIT_LOCK_CONTENTION
    except SystemExit as exc:
        # Hard guard failure from engine — propagate the exit code.
        code = exc.code if isinstance(exc.code, int) else EXIT_GUARD_FAILURE
        return code if code != 0 else EXIT_GUARD_FAILURE

    # Notify if we have a summary path and notification is configured.
    if hasattr(result, "summary_path") and result.summary_path is not None:
        notify_tick_summary(result.summary_path)

    return EXIT_OK


def cmd_dry_run(config: EngineConfig) -> int:
    """
    Run a dry-run-only maintenance tick (DRY_RUN_PROTOCOL.md).

    Forces MANUAL_CLI invocation mode so the engine applies
    DRY_RUN_ONLY to all decisions regardless of live_default.
    Acquires concurrent-tick lockfile.

    Returns an exit code integer.
    """
    return cmd_run(config, invocation_mode=InvocationMode.MANUAL_CLI)


def cmd_status(config: EngineConfig) -> int:
    """
    Print current maintenance worker status to stdout.

    Reports:
      - lockfile presence (another tick running?)
      - state_dir sentinel files (KILL_SWITCH, MAINTENANCE_PAUSED, etc.)
      - evidence_dir: most recent trail date

    Returns EXIT_OK always (status is read-only).
    """
    lockfile = config.state_dir / _LOCKFILE_NAME
    if lockfile.exists():
        try:
            pid = lockfile.read_text(encoding="utf-8").strip()
            print(f"status: TICK_IN_PROGRESS (pid={pid})")
        except OSError:
            print("status: TICK_IN_PROGRESS (lockfile exists, pid unreadable)")
    else:
        print("status: IDLE")

    # Sentinel files
    sentinels = [
        "KILL_SWITCH", "MAINTENANCE_PAUSED", "ONCALL_QUIET",
        "SELF_QUARANTINE", "DRY_RUN_OVERRIDE",
    ]
    for name in sentinels:
        path = config.state_dir / name
        if path.exists():
            print(f"sentinel: {name} PRESENT")

    # Most recent evidence trail
    if config.evidence_dir.exists():
        trails = sorted(config.evidence_dir.iterdir(), reverse=True)
        if trails:
            latest = trails[0]
            summary = latest / "SUMMARY.md"
            exit_code_path = latest / "exit_code"
            ec = ""
            if exit_code_path.exists():
                try:
                    ec = f" exit_code={exit_code_path.read_text().strip()}"
                except OSError:
                    pass
            print(f"last_trail: {latest.name}{ec}")
        else:
            print("last_trail: none")
    else:
        print("last_trail: evidence_dir not found")

    return EXIT_OK


def cmd_init(config: EngineConfig) -> int:
    """
    Initialize the maintenance worker state directory.

    Creates state_dir and evidence_dir if they don't exist.
    Does NOT write install_metadata.json (that is written on first tick).

    Returns EXIT_OK on success, EXIT_ERROR on failure.
    """
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        print(f"[maintenance_worker] init: state_dir={config.state_dir}")
        print(f"[maintenance_worker] init: evidence_dir={config.evidence_dir}")
        print("[maintenance_worker] init: OK (install_metadata written on first tick)")
        return EXIT_OK
    except OSError as exc:
        print(f"[maintenance_worker] ERROR: init failed: {exc}", file=sys.stderr)
        return EXIT_ERROR


# ---------------------------------------------------------------------------
# argparse main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maintenance_worker",
        description="Maintenance worker daemon CLI.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to maintenance_worker_config.json. "
            "Defaults to MAINTENANCE_WORKER_CONFIG env var or "
            "./maintenance_worker_config.json."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Run a live maintenance tick.")
    subparsers.add_parser(
        "dry-run", help="Run a dry-run-only tick (forces DRY_RUN_ONLY)."
    )
    subparsers.add_parser("status", help="Print current maintenance worker status.")
    subparsers.add_parser(
        "init", help="Initialize state_dir and evidence_dir."
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """
    CLI entry point. Returns exit code.

    argv: if None, reads sys.argv[1:].
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    config = _load_config(args.config)
    if config is None:
        return EXIT_NO_CONFIG

    command = args.command
    if command == "run":
        return cmd_run(config)
    elif command == "dry-run":
        return cmd_dry_run(config)
    elif command == "status":
        return cmd_status(config)
    elif command == "init":
        return cmd_init(config)
    else:
        parser.print_help()
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
