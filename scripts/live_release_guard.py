#!/usr/bin/env python3
# Lifecycle: created=2026-07-22; last_reviewed=2026-07-22; last_reused=never
# Purpose: verify the immutable live-release boundary without writing the checkout.
# Reuse: called by deploy_live before restart; use `status` during a guarded-release rollout.
# Authority basis: AGENTS.md §5 live-branch change control; operator directive 2026-07-22.
"""Read-only attestation for an immutable Zeus live release.

The boundary is deliberately physical, not another advisory Git hook.  A
root-owned release checkout is marked by ``.zeus-release.json`` and uses only
two landing lanes:

* ``pick``: the privileged release tool cherry-picks a published hot-fix;
* ``merged_pr``: the privileged release tool synchronizes ``origin/live``.

The normal operator account can read that checkout, but cannot write its code,
Git metadata, launch configuration, or virtual environment.  ``state/`` and
``logs/`` live outside the release and are the only writable runtime surfaces.

This module never creates the boundary.  Provisioning needs root and GitHub
ruleset authority, so it must be a separately reviewed, explicitly invoked
operator action.  Keeping this checker read-only makes it safe to run before
and after the cutover and avoids a rollout-time false positive in legacy trees.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MARKER = ".zeus-release.json"
LANES = frozenset({"pick", "merged_pr"})


@dataclass(frozen=True)
class ReleaseGuardStatus:
    active: bool
    ready: bool
    detail: str
    release_sha: str | None = None
    lane: str | None = None


def _sha(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) == 40 and all(ch in "0123456789abcdef" for ch in text):
        return text
    return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=20.0,
    )


def inspect_release(repo: Path) -> ReleaseGuardStatus:
    """Return legacy-inactive, ready, or a fail-closed guarded-release status."""

    marker = repo / MARKER
    if not marker.exists():
        return ReleaseGuardStatus(False, True, "release guard not installed")
    try:
        payload: dict[str, Any] = json.loads(marker.read_text())
    except (OSError, ValueError, TypeError) as exc:
        return ReleaseGuardStatus(True, False, f"invalid release marker: {type(exc).__name__}")
    if not isinstance(payload, dict):
        return ReleaseGuardStatus(True, False, "invalid release marker: expected object")
    release_sha = _sha(payload.get("release_sha"))
    upstream_sha = _sha(payload.get("upstream_live_sha"))
    lane = str(payload.get("landing_lane") or "")
    if release_sha is None or upstream_sha is None or lane not in LANES:
        return ReleaseGuardStatus(True, False, "invalid release marker fields")
    head = _git(repo, "rev-parse", "HEAD")
    local_sha = _sha(head.stdout)
    if head.returncode != 0 or local_sha != release_sha:
        return ReleaseGuardStatus(True, False, "release SHA differs from immutable marker", release_sha, lane)
    remote = _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/live")
    remote_sha = _sha(remote.stdout.split()[0] if remote.stdout.split() else "")
    if remote.returncode != 0 or remote_sha is None:
        return ReleaseGuardStatus(True, False, "cannot read origin/live (fail-closed)", release_sha, lane)
    if remote_sha != upstream_sha or remote_sha != release_sha:
        return ReleaseGuardStatus(True, False, "origin/live differs from release marker", release_sha, lane)
    return ReleaseGuardStatus(True, True, f"immutable release verified via {lane}", release_sha, lane)


def _command_status(args: argparse.Namespace) -> int:
    result = inspect_release(Path(args.repo).resolve())
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        state = "READY" if result.ready else "BLOCKED"
        scope = "guarded" if result.active else "legacy"
        print(f"{state} {scope}: {result.detail}")
    return 0 if result.ready else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only immutable live-release guard")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="verify immutable release marker and origin/live")
    status.add_argument("--repo", default=".", help="release checkout to inspect")
    status.add_argument("--json", action="store_true", help="emit JSON")
    status.set_defaults(func=_command_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
