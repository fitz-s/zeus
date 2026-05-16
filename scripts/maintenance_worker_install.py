# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis:
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/05_execution_packets/PACKET_INDEX.md §P6
#   docs/operations/task_2026-05-15_runtime_improvement_engineering_package/02_daily_maintenance_agent/DRY_RUN_PROTOCOL.md
#   bindings/zeus/config.yaml
#
# IDEMPOTENT: safe to re-run. Re-run does NOT reset first_run_at.
# Re-run refreshes the plist and config.json if the templates changed,
# but preserves install_metadata.json (ImmutableMetadataError = no-op on re-run).
"""
maintenance_worker_install.py — Zeus maintenance_worker install helper.

Installs the maintenance worker for Zeus:
  1. Creates state_dir and evidence_dir.
  2. Writes maintenance_worker_config.json to state_dir (overwrites on re-run).
  3. Writes install_metadata.json to state_dir (idempotent: no-op if exists).
  4. Copies the launchd plist with ZEUS_REPO_PLACEHOLDER substituted to
     ~/Library/LaunchAgents/com.zeus.maintenance.plist.
  5. Prints `launchctl load` command for human to run (NOT auto-loaded).

Modes:
  --dry-run         Print what would happen; do not write anything.
  --run             Execute the install.
  --repo-root PATH  Override repo root detection (defaults to the directory
                    containing this script's parent directory).

The --run mode is used by the launchd plist itself to invoke the maintenance
worker CLI (via MAINTENANCE_WORKER_CONFIG env var).

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAUNCHD_LABEL = "com.zeus.maintenance"
PLIST_DEST_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_FILENAME = f"{LAUNCHD_LABEL}.plist"
PLIST_TEMPLATE = "bindings/zeus/launchd_plist.plist"
CONFIG_FILENAME = "maintenance_worker_config.json"
INSTALL_METADATA_FILENAME = "install_metadata.json"
AGENT_VERSION = "0.1.0"
PLACEHOLDER = "ZEUS_REPO_PLACEHOLDER"


# ---------------------------------------------------------------------------
# Repo root detection
# ---------------------------------------------------------------------------


def detect_repo_root(override: str | None = None) -> Path:
    """
    Return the Zeus repo root.

    If override is given, use it. Otherwise, walk up from this script's
    location until we find a directory containing AGENTS.md (Zeus repo signal).
    Fails with sys.exit(1) if not found.
    """
    if override:
        root = Path(override).resolve()
        if not root.is_dir():
            _die(f"--repo-root {override!r} is not a directory")
        return root

    candidate = Path(__file__).resolve().parent  # scripts/
    while candidate != candidate.parent:
        if (candidate / "AGENTS.md").exists() and (candidate / "maintenance_worker").is_dir():
            return candidate
        candidate = candidate.parent

    _die(
        "Cannot detect Zeus repo root. Run from within the repo or pass --repo-root."
    )


def _die(msg: str) -> None:
    print(f"[install] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Plist generation from template
# ---------------------------------------------------------------------------


def resolve_plist(plist_template_path: Path, repo_root: Path) -> str:
    """
    Read plist template and substitute ZEUS_REPO_PLACEHOLDER with repo_root.
    Returns the rendered plist XML string.
    """
    template = plist_template_path.read_text(encoding="utf-8")
    return template.replace(PLACEHOLDER, str(repo_root))


# ---------------------------------------------------------------------------
# Config JSON generation
# ---------------------------------------------------------------------------


def build_engine_config(repo_root: Path) -> dict:
    """
    Build the maintenance_worker_config.json dict from Zeus binding config.

    Mirrors EngineConfig fields from maintenance_worker/types/specs.py.
    Paths are absolute strings.
    """
    state_dir = repo_root / "state" / "maintenance_state"
    evidence_dir = repo_root / "state" / "maintenance_evidence"
    task_catalog = (
        repo_root
        / "docs/operations/task_2026-05-15_runtime_improvement_engineering_package"
        / "02_daily_maintenance_agent/TASK_CATALOG.yaml"
    )
    safety_contract = (
        repo_root
        / "docs/operations/task_2026-05-15_runtime_improvement_engineering_package"
        / "02_daily_maintenance_agent/SAFETY_CONTRACT.md"
    )
    return {
        "repo_root": str(repo_root),
        "state_dir": str(state_dir),
        "evidence_dir": str(evidence_dir),
        "task_catalog_path": str(task_catalog),
        "safety_contract_path": str(safety_contract),
        "live_default": False,  # always false until 30-day floor expires
        "scheduler": "launchd",
        "notification_channel": "discord",
        "env_vars": {
            "ZEUS_REPO": str(repo_root),
            "PYTHONPATH": str(repo_root),
        },
    }


# ---------------------------------------------------------------------------
# Install metadata
# ---------------------------------------------------------------------------


def _detect_git_remote(repo_root: Path) -> str:
    """Return the git remote origin URL, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def build_install_metadata(repo_root: Path) -> dict:
    """Build install_metadata.json content for first-write."""
    remote_url = _detect_git_remote(repo_root)
    return {
        "schema_version": 1,
        "first_run_at": datetime.now(tz=timezone.utc).isoformat(),
        "agent_version": AGENT_VERSION,
        "install_run_id": str(uuid.uuid4()),
        "allowed_remote_urls": [remote_url] if remote_url else [],
        "repo_root_at_install": str(repo_root),
    }


# ---------------------------------------------------------------------------
# Idempotent install steps
# ---------------------------------------------------------------------------


def step_create_dirs(state_dir: Path, evidence_dir: Path, dry_run: bool) -> None:
    for d in [state_dir, evidence_dir]:
        if d.exists():
            _log(f"  [skip] dir exists: {d}")
        else:
            _log(f"  [create] mkdir -p {d}")
            if not dry_run:
                d.mkdir(parents=True, exist_ok=True)


def step_write_config(state_dir: Path, config: dict, dry_run: bool) -> None:
    target = state_dir / CONFIG_FILENAME
    payload = json.dumps(config, indent=2) + "\n"
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing == payload:
            _log(f"  [skip] config unchanged: {target}")
            return
        _log(f"  [update] config changed: {target}")
    else:
        _log(f"  [create] config: {target}")
    if not dry_run:
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(target)


def step_write_install_metadata(state_dir: Path, repo_root: Path, dry_run: bool) -> None:
    target = state_dir / INSTALL_METADATA_FILENAME
    if target.exists():
        _log(f"  [skip] install_metadata.json already exists (immutable): {target}")
        # Verify schema_version is readable
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            first_run = raw.get("first_run_at", "UNKNOWN")
            _log(f"         first_run_at={first_run}")
        except Exception:
            pass
        return
    _log(f"  [create] install_metadata.json: {target}")
    if not dry_run:
        metadata = build_install_metadata(repo_root)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        _log(f"         first_run_at={metadata['first_run_at']}")
        _log(f"         30-day dry-run floor starts now")


def step_install_plist(
    plist_template_path: Path,
    repo_root: Path,
    dry_run: bool,
) -> None:
    dest = PLIST_DEST_DIR / PLIST_FILENAME
    rendered = resolve_plist(plist_template_path, repo_root)

    if dest.exists():
        existing = dest.read_text(encoding="utf-8")
        if existing == rendered:
            _log(f"  [skip] plist unchanged: {dest}")
            return
        _log(f"  [update] plist content changed: {dest}")
    else:
        _log(f"  [create] plist: {dest}")

    if not dry_run:
        PLIST_DEST_DIR.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".plist.tmp")
        tmp.write_text(rendered, encoding="utf-8")
        tmp.replace(dest)

    _log(f"  [MANUAL] To activate, run:")
    _log(f"           launchctl load -w {dest}")
    _log(f"  NOTE: Do NOT run launchctl load now — wait for first manual dry-run")
    _log(f"        to confirm install_metadata.json is correct.")


# ---------------------------------------------------------------------------
# --run mode: invoked by launchd plist to run the maintenance worker
# ---------------------------------------------------------------------------


def run_worker(repo_root: Path) -> int:
    """
    Called by launchd via the plist's ProgramArguments. Delegates to the
    maintenance_worker CLI entry point.
    """
    config_path = repo_root / "state" / "maintenance_state" / CONFIG_FILENAME
    if not config_path.exists():
        print(
            f"[install] ERROR: config not found: {config_path}\n"
            "Run `python3 scripts/maintenance_worker_install.py --run-install` first.",
            file=sys.stderr,
        )
        return 1

    # Delegate to maintenance_worker.cli.entry.main()
    try:
        from maintenance_worker.cli.entry import main as mw_main
        return mw_main(["--config", str(config_path), "run"])
    except ImportError as exc:
        print(
            f"[install] ERROR: cannot import maintenance_worker: {exc}\n"
            "Ensure PYTHONPATH includes the Zeus repo root.",
            file=sys.stderr,
        )
        return 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    print(msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maintenance_worker_install.py",
        description="Install Zeus maintenance worker (idempotent).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing any files.",
    )
    mode.add_argument(
        "--run-install",
        action="store_true",
        help="Execute the full install.",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="Run the maintenance worker (invoked by launchd plist).",
    )
    parser.add_argument(
        "--repo-root",
        metavar="PATH",
        default=None,
        help="Override Zeus repo root detection.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root = detect_repo_root(args.repo_root)

    if args.run:
        return run_worker(repo_root)

    dry_run = args.dry_run  # True for --dry-run, False for --run-install
    mode_label = "DRY-RUN" if dry_run else "INSTALL"

    _log(f"[maintenance_worker_install] {mode_label} — repo_root={repo_root}")
    _log("")

    state_dir = repo_root / "state" / "maintenance_state"
    evidence_dir = repo_root / "state" / "maintenance_evidence"
    plist_template = repo_root / PLIST_TEMPLATE

    if not plist_template.exists():
        _die(f"plist template not found: {plist_template}")

    _log("Step 1: Create state and evidence directories")
    step_create_dirs(state_dir, evidence_dir, dry_run)
    _log("")

    _log("Step 2: Write maintenance_worker_config.json")
    config = build_engine_config(repo_root)
    step_write_config(state_dir, config, dry_run)
    _log("")

    _log("Step 3: Write install_metadata.json (immutable after first write)")
    step_write_install_metadata(state_dir, repo_root, dry_run)
    _log("")

    _log("Step 4: Install launchd plist")
    step_install_plist(plist_template, repo_root, dry_run)
    _log("")

    if dry_run:
        _log("[maintenance_worker_install] DRY-RUN complete — no files written.")
    else:
        _log("[maintenance_worker_install] INSTALL complete.")
        _log("")
        _log("Next steps:")
        _log("  1. Review state/maintenance_state/install_metadata.json")
        _log(f"  2. Validate plist: plutil -lint {PLIST_DEST_DIR / PLIST_FILENAME}")
        _log(f"  3. Load agent:     launchctl load -w {PLIST_DEST_DIR / PLIST_FILENAME}")
        _log("  4. First tick runs at 04:30 local time tomorrow.")
        _log("  5. 30-day dry-run floor is now active — all tasks run in dry-run mode.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
