#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: external-review mediums inventory 2026-06-12 (settings-consumer audit)
"""
Settings-consumer audit: detect orphan keys and deleted-flag resurrections.

Parses config/settings.json for all non-documentation leaf keys (dotted paths).
For each leaf key name, greps src/ for any consumer references.

Reports:
  ORPHAN  — leaf key with zero src/ references (WARNING, no CI fail)
  RESURRECTION — a key from DELETED_KEYS_DENY_LIST still has src/ consumers (EXIT 1)

Exit codes:
    0 — no resurrections found (orphans printed as warnings only)
    1 — one or more deleted-key resurrections found
    2 — IO / parse error
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Deleted flags: any src/ consumer for these keys is a resurrection (exit 1).
# Keys that were intentionally removed from settings.json must never be
# silently re-read in src/.
# ---------------------------------------------------------------------------
DELETED_KEYS_DENY_LIST: frozenset[str] = frozenset([
    "live_canary_enabled",
    "taker_fok_fak_live_enabled",
    "forecast_sharpness_gate_enabled",
    "stale_book_directional_trading_enabled",
    "mainstream_agreement_enforce_on_submit",
    "replacement_qlcb_settlement_sigma_floor_enabled",
])

# Pattern that a src/ reference must match: key appears as a string literal
# (quoted) or as a dict-access / attribute pattern.  We use a broad match:
# the literal key string anywhere in src/ text (covers .get("key"), ["key"],
# settings.key, and getattr(_, "key") forms).
_KEY_LITERAL_RE_TMPL = r'["\']{}["\']'


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _grep_key(src_dir: Path, key: str) -> list[str]:
    """Return list of 'file:line' hits for key as a string literal in src_dir."""
    pattern = _KEY_LITERAL_RE_TMPL.format(re.escape(key))
    try:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "-E", pattern, str(src_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode not in (0, 1):
            # returncode=1 means no matches (grep convention), 0=matches
            # anything else is an error
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []


def _extract_leaf_keys(obj: object, prefix: str = "") -> list[str]:
    """Recursively extract all leaf key names (last segment) from a JSON object."""
    if isinstance(obj, dict):
        keys: list[str] = []
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                keys.extend(_extract_leaf_keys(v, path))
            else:
                keys.append(path)
        return keys
    if isinstance(obj, list):
        keys = []
        for i, item in enumerate(obj):
            path = f"{prefix}[{i}]"
            if isinstance(item, (dict, list)):
                keys.extend(_extract_leaf_keys(item, path))
            else:
                keys.append(path)
        return keys
    return [prefix] if prefix else []


def _leaf_key_name(dotted_path: str) -> str:
    """Extract the leaf segment name from a dotted path (ignoring array indices)."""
    parts = re.split(r"[.\[]", dotted_path)
    non_index = [p.rstrip("]") for p in parts if p and not p.isdigit() and p.rstrip("]")]
    return non_index[-1] if non_index else dotted_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Settings-consumer audit (orphans + resurrections).")
    ap.add_argument(
        "--settings",
        default=None,
        help="Path to settings.json (default: config/settings.json relative to repo root)",
    )
    ap.add_argument(
        "--src",
        default=None,
        help="Path to src/ directory to grep (default: src/ relative to repo root)",
    )
    args = ap.parse_args(argv)

    repo = _repo_root()
    settings_path = Path(args.settings) if args.settings else repo / "config" / "settings.json"
    src_dir = Path(args.src) if args.src else repo / "src"

    try:
        with settings_path.open() as fh:
            settings = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {settings_path}: {exc}", file=sys.stderr)
        return 2

    # Extract all leaf paths and their key names.
    leaf_paths = _extract_leaf_keys(settings)

    # Deduplicate by key name; skip documentation keys (prefix "_") and
    # pure integer paths (array indices).
    seen: set[str] = set()
    unique_keys: list[str] = []
    for path in leaf_paths:
        name = _leaf_key_name(path)
        # Skip doc notes (operator convention: keys starting with "_")
        if name.startswith("_"):
            continue
        # Skip bare integers (array value indices, not key names)
        if name.isdigit():
            continue
        if name not in seen:
            seen.add(name)
            unique_keys.append(name)

    print(f"settings.json: {len(unique_keys)} unique non-doc leaf keys to audit")
    print(f"src/ directory: {src_dir}")
    print()

    orphans: list[str] = []
    resurrections: list[tuple[str, list[str]]] = []

    for key in sorted(unique_keys):
        hits = _grep_key(src_dir, key)
        if not hits:
            orphans.append(key)
        # Check resurrection regardless of whether it's also an orphan
        # (a deleted key should have ZERO consumers, so orphan + deleted = fine;
        # non-orphan + deleted = resurrection).
        if key in DELETED_KEYS_DENY_LIST and hits:
            resurrections.append((key, hits))

    # Also check deleted keys that may not be in settings.json at all (fully removed).
    for deleted_key in sorted(DELETED_KEYS_DENY_LIST):
        if deleted_key not in seen:
            # Not in settings.json — still check for src/ consumers.
            hits = _grep_key(src_dir, deleted_key)
            if hits:
                # Avoid double-reporting if already caught above.
                already = any(k == deleted_key for k, _ in resurrections)
                if not already:
                    resurrections.append((deleted_key, hits))

    # --- Report orphans (WARNING only, no exit 1) ---
    print(f"ORPHAN KEYS (zero src/ consumers) — {len(orphans)} total:")
    if orphans:
        for k in orphans:
            print(f"  WARNING: ORPHAN  {k}")
    else:
        print("  (none)")
    print()

    # --- Report resurrections (EXIT 1) ---
    print(f"RESURRECTION CHECK — deleted-key deny-list ({len(DELETED_KEYS_DENY_LIST)} keys):")
    if resurrections:
        print(f"  FAIL: {len(resurrections)} deleted key(s) still have src/ consumers:")
        for key, hits in resurrections:
            print(f"    RESURRECTION  {key!r}  ({len(hits)} hit(s)):")
            for h in hits[:5]:
                print(f"      {h}")
            if len(hits) > 5:
                print(f"      ... +{len(hits) - 5} more")
        return 1
    else:
        for dk in sorted(DELETED_KEYS_DENY_LIST):
            print(f"  OK (no consumers): {dk}")
        print()
        print("PASS: no deleted-key resurrections found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
