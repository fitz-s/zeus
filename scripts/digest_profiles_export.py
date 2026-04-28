#!/usr/bin/env python3
# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Generate/check the derived Python digest_profiles mirror from canonical architecture/topology.yaml.
# Reuse: Run with --check in CI or without --check only when regenerating the derived mirror after topology digest profile edits.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §2.1 D1 + DEEP_PLAN §4.2 #14 + Tier 2
# Phase 3 ITEM #14-followup dispatch (digest_profiles → Python; smallest-diff
# migration scaffolding). Per audit-first methodology (Phase 2 lesson):
# moving 142KB YAML to 142KB Python doesn't reduce surface unless equivalence
# is proven first AND operator approves the truth-source flip.
"""Export architecture/topology.yaml :: digest_profiles section to Python.

Generates `architecture/digest_profiles.py` from the YAML data so that:

  (a) equivalence tests can assert byte-for-byte fidelity between YAML and
      Python representations;
  (b) when operator approves Phase 3.5 (truth-source flip from YAML to Python),
      topology_doctor can `from architecture.digest_profiles import PROFILES`
      instead of `topology.get("digest_profiles")`;
  (c) CI can regenerate the Python file from YAML on every change to keep
      both surfaces in sync until the truth-source decision is made.

This script is the SCAFFOLDING for Phase 3.5; it does NOT yet flip the truth
source. Phase 3 close-state: YAML remains canonical; Python is a derived
mirror; equivalence test ensures they cannot drift.

Usage:
    python3 scripts/digest_profiles_export.py [--check]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOPOLOGY = REPO_ROOT / "architecture" / "topology.yaml"
EXPORT = REPO_ROOT / "architecture" / "digest_profiles.py"

HEADER = '''"""Auto-generated digest profiles for topology_doctor.

DO NOT EDIT BY HAND. This file is regenerated from architecture/topology.yaml
:: digest_profiles by scripts/digest_profiles_export.py.

Phase 3 status (2026-04-28): YAML is canonical truth source; this file is a
derived mirror enabling Python-side import + equivalence-test antibody. Phase
3.5+ may flip the truth source pending operator approval.
"""
from __future__ import annotations

PROFILES: list[dict] = '''


def _yaml_load_profiles():
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        print("PyYAML required", file=sys.stderr)
        sys.exit(2)
    doc = yaml.safe_load(TOPOLOGY.read_text())
    return doc.get("digest_profiles", []) or []


def _render_python(profiles: list) -> str:
    """Render the profiles as Python source. Use repr() for fidelity."""
    import pprint  # noqa: PLC0415
    body = pprint.pformat(profiles, indent=2, width=120, sort_dicts=False)
    return HEADER + body + "\n"


def export() -> bool:
    """Write the Python file. Returns True if a change occurred."""
    profiles = _yaml_load_profiles()
    new_text = _render_python(profiles)
    if EXPORT.exists() and EXPORT.read_text() == new_text:
        return False
    EXPORT.write_text(new_text)
    return True


def check() -> bool:
    """Verify the Python file is in sync with YAML. Returns True if in sync."""
    profiles = _yaml_load_profiles()
    expected = _render_python(profiles)
    if not EXPORT.exists():
        return False
    return EXPORT.read_text() == expected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="Verify .py is in sync with YAML; exit 1 if drift")
    args = ap.parse_args()

    if args.check:
        in_sync = check()
        if in_sync:
            print(f"OK: {EXPORT.relative_to(REPO_ROOT)} matches YAML")
            return 0
        print(f"DRIFT: {EXPORT.relative_to(REPO_ROOT)} does not match YAML; "
              "run without --check to regenerate", file=sys.stderr)
        return 1

    changed = export()
    n = len(_yaml_load_profiles())
    if changed:
        print(f"Regenerated {EXPORT.relative_to(REPO_ROOT)} ({n} profiles)")
    else:
        print(f"No change: {EXPORT.relative_to(REPO_ROOT)} already current ({n} profiles)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
