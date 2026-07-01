#!/usr/bin/env python3
"""Restore the active live-trading LaunchAgent plist from an audited backup.

Default mode is dry-run.  ``--apply`` copies a validated
``com.zeus.live-trading.plist.*`` source back to the active
``com.zeus.live-trading.plist`` path.  This script never loads, bootstraps, or
kickstarts launchd; run restart preflight again before starting live-trading.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


LABEL = "com.zeus.live-trading"
ACTIVE_NAME = f"{LABEL}.plist"


@dataclass(frozen=True)
class Candidate:
    path: str
    mtime: float
    label: str | None
    program_arguments: list[str]
    working_directory: str | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the active plist")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    parser.add_argument("--source", metavar="PATH", help="Explicit source plist to restore")
    parser.add_argument(
        "--launchagents-dir",
        default=str(Path.home() / "Library" / "LaunchAgents"),
        metavar="PATH",
    )
    return parser.parse_args()


def _load_candidate(path: Path) -> Candidate | None:
    try:
        payload = plistlib.loads(path.read_bytes())
    except Exception:
        return None
    args = payload.get("ProgramArguments")
    args_list = [str(arg) for arg in args] if isinstance(args, list) else []
    label = payload.get("Label")
    working_directory = payload.get("WorkingDirectory")
    if label != LABEL:
        return None
    if "-m" not in args_list or "src.main" not in args_list:
        return None
    return Candidate(
        path=str(path),
        mtime=path.stat().st_mtime,
        label=str(label),
        program_arguments=args_list,
        working_directory=str(working_directory) if working_directory else None,
    )


def _candidate_paths(launchagents_dir: Path) -> list[Path]:
    paths = [
        path
        for path in launchagents_dir.glob(f"{ACTIVE_NAME}.*")
        if path.is_file()
    ]
    # Prefer intentionally disabled live plists over old generic backups.
    return sorted(
        paths,
        key=lambda path: (
            1 if ".disabled" in path.name else 0,
            path.stat().st_mtime,
            path.name,
        ),
        reverse=True,
    )


def select_candidate(
    *,
    launchagents_dir: Path,
    source: Path | None = None,
) -> tuple[Candidate | None, list[Candidate]]:
    paths = [source] if source is not None else _candidate_paths(launchagents_dir)
    candidates = [
        candidate
        for path in paths
        if path is not None
        for candidate in [_load_candidate(path.expanduser().resolve())]
        if candidate is not None
    ]
    return (candidates[0] if candidates else None, candidates)


def restore_launchagent(
    *,
    launchagents_dir: Path,
    source: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    launchagents_dir = launchagents_dir.expanduser().resolve()
    active_path = launchagents_dir / ACTIVE_NAME
    selected, candidates = select_candidate(
        launchagents_dir=launchagents_dir,
        source=source,
    )
    result: dict[str, Any] = {
        "ok": selected is not None and (not apply or not active_path.exists()),
        "apply": apply,
        "active_path": str(active_path),
        "active_exists": active_path.exists(),
        "selected": asdict(selected) if selected else None,
        "candidate_count": len(candidates),
        "candidates": [asdict(candidate) for candidate in candidates[:10]],
        "launchctl_action": "none",
    }
    if selected is None:
        result["reason"] = "no_valid_live_trading_launchagent_backup"
        return result
    if active_path.exists():
        result["reason"] = "active_live_trading_launchagent_already_exists"
        return result
    if not apply:
        result["reason"] = "dry_run"
        return result
    active_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(selected.path, active_path)
    active_path.chmod(0o644)
    result["active_exists_after"] = active_path.exists()
    result["reason"] = "restored_active_launchagent"
    result["ok"] = active_path.exists()
    return result


def main() -> int:
    args = _parse_args()
    result = restore_launchagent(
        launchagents_dir=Path(args.launchagents_dir),
        source=Path(args.source) if args.source else None,
        apply=bool(args.apply),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] restore_live_trading_launchagent")
        print(f"  ok          : {result['ok']}")
        print(f"  active_path : {result['active_path']}")
        print(f"  reason      : {result.get('reason')}")
        selected = result.get("selected") or {}
        print(f"  selected    : {selected.get('path')}")
        print("  launchctl   : none")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
