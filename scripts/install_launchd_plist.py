#!/usr/bin/env python3
# Created: 2026-07-28
# Last reused or audited: 2026-07-28
# Authority basis: showcase brief 06 (operator-path portability) review round 1 —
#   raw `sed` substitution into a launchd plist breaks on paths containing the
#   sed delimiter/&/backslashes (and this repo has a directory named
#   "51 source data"), and text substitution cannot verify the result is a
#   well-formed plist before it lands in ~/Library/LaunchAgents.
"""install_launchd_plist.py — render + install a deploy/launchd/*.plist template.

Templates under deploy/launchd/ use two placeholder tokens instead of a
baked-in operator path:
    ZEUS_REPO_PLACEHOLDER  -> the repo's absolute path
    ZEUS_HOME_PLACEHOLDER  -> $HOME

WHY plistlib, not sed: a plist is structured XML. Raw text substitution
(`sed 's#PLACEHOLDER#/some/path#g'`) breaks if the replacement path contains
the delimiter character, `&`, or backslashes — and cannot prove the file it
just edited is still a valid plist. This script parses the template with
plistlib, replaces the placeholder substrings ONLY inside string values (not
keys, not structure), refuses to write if any placeholder survives the
substitution anywhere in the tree, `plutil -lint`s the rendered bytes before
they touch disk, and writes atomically (temp file in the destination
directory + os.replace).

USAGE
    python3 scripts/install_launchd_plist.py deploy/launchd/com.zeus.data-ingest.plist
    python3 scripts/install_launchd_plist.py deploy/launchd/com.zeus.live-trading.plist --dry-run

Default destination: ~/Library/LaunchAgents/<template basename>. Loading the
result is a separate, explicit operator step (`launchctl bootstrap ...`) —
this script only renders and installs the file.

Stdlib only (plistlib, subprocess for plutil).
"""
from __future__ import annotations

import argparse
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_PLACEHOLDER = "ZEUS_REPO_PLACEHOLDER"
HOME_PLACEHOLDER = "ZEUS_HOME_PLACEHOLDER"

# Matches ANY placeholder-shaped token, not just the two this script knows how
# to fill in — so a future template that introduces a third placeholder (and
# forgets to teach this script about it) fails closed instead of shipping the
# literal token into ~/Library/LaunchAgents.
_PLACEHOLDER_SHAPE_RE = re.compile(r"ZEUS_[A-Z0-9_]*PLACEHOLDER")


def _substitute(value: Any, repo_root: str, home: str) -> Any:
    """Replace placeholder substrings inside every string value, recursively."""
    if isinstance(value, str):
        return value.replace(REPO_PLACEHOLDER, repo_root).replace(HOME_PLACEHOLDER, home)
    if isinstance(value, dict):
        return {key: _substitute(item, repo_root, home) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, repo_root, home) for item in value]
    return value


def _find_unresolved(value: Any, path: str = "root") -> list[tuple[str, str]]:
    """Return [(location, value)] for any string still containing a placeholder token.

    Checks the generic ZEUS_*PLACEHOLDER shape, not just the two tokens this
    script substitutes — a template that introduces a new placeholder this
    script doesn't know about must fail closed, not ship the literal token.
    A real-world repo/home path could also theoretically contain the literal
    placeholder text (astronomically unlikely, but this is a money-path
    deployment artifact, so fail closed rather than assume).
    """
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        if _PLACEHOLDER_SHAPE_RE.search(value):
            found.append((path, value))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_find_unresolved(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_unresolved(item, f"{path}[{index}]"))
    return found


def render(template_path: Path, repo_root: Path, home: Path) -> bytes:
    """Parse the template, substitute placeholders, and return rendered plist bytes.

    Raises SystemExit if any placeholder token survives substitution.
    """
    with open(template_path, "rb") as f:
        payload = plistlib.load(f)
    rendered = _substitute(payload, str(repo_root), str(home))
    unresolved = _find_unresolved(rendered)
    if unresolved:
        detail = "; ".join(f"{location}={text!r}" for location, text in unresolved)
        raise SystemExit(
            f"REFUSING to render {template_path}: unresolved placeholder(s) after "
            f"substitution: {detail}"
        )
    return plistlib.dumps(rendered)


def _plutil_lint(body: bytes, *, label: str) -> None:
    """Validate rendered plist bytes with plutil before they touch disk.

    Raises SystemExit on lint failure. Skips (with a warning) when `plutil`
    is unavailable — e.g. non-macOS CI — since the plist still round-trips
    through plistlib.dumps/load correctly in that case.
    """
    plutil = shutil_which("plutil")
    if plutil is None:
        print(f"WARNING: plutil not found; skipping lint of {label}", file=sys.stderr)
        return
    fd, tmp_path = tempfile.mkstemp(suffix=".plist")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
        result = subprocess.run([plutil, "-lint", tmp_path], capture_output=True, text=True)
    finally:
        os.unlink(tmp_path)
    if result.returncode != 0:
        raise SystemExit(
            f"REFUSING to install {label}: plutil -lint failed:\n"
            f"{result.stdout}{result.stderr}"
        )


def shutil_which(cmd: str) -> str | None:
    import shutil

    return shutil.which(cmd)


def install(
    template_path: Path,
    dest: Path,
    *,
    repo_root: Path,
    home: Path,
) -> None:
    body = render(template_path, repo_root, home)
    _plutil_lint(body, label=str(template_path))

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    print(f"installed {dest} (rendered from {template_path})")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_launchd_plist.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("template", type=Path, help="path to a deploy/launchd/*.plist template")
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="install destination (default: ~/Library/LaunchAgents/<template basename>)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="override repo root used for ZEUS_REPO_PLACEHOLDER (default: this script's repo)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="render + lint only; print the rendered plist to stdout, write nothing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.template.exists():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 1

    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).resolve()
    home = Path.home()
    dest = args.dest or (home / "Library" / "LaunchAgents" / args.template.name)

    if args.dry_run:
        body = render(args.template, repo_root, home)
        _plutil_lint(body, label=str(args.template))
        sys.stdout.buffer.write(body)
        return 0

    install(args.template, dest, repo_root=repo_root, home=home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
