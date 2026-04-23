# Created: 2026-04-23
# Last reused/audited: 2026-04-23
# Authority basis: T1.e of midstream remediation packet
# (docs/operations/task_2026-04-23_midstream_remediation/plan.md);
# enforces the T1.a (commit 67b5908) dated-header guarantee over
# the midstream guardian panel registered in
# architecture/test_topology.yaml::midstream_guardian_panel.

"""CI audit — every midstream guardian test file carries a dated provenance header.

Reads the panel file list from `architecture/test_topology.yaml` under
the `midstream_guardian_panel` key. For each listed file, inspects the
first few lines for the three canonical provenance markers:

- `# Created: YYYY-MM-DD`
- `# Last reused/audited: YYYY-MM-DD`
- `# Authority basis: <...>`

Exits 0 when every panel file carries all three markers; exits 1 with a
list of missing-marker files otherwise.

Rationale: per `/Users/leofitz/CLAUDE.md` §"File-header provenance rule"
and the operator's test-currency directive, any test without a dated
provenance header AND recent passing-run evidence is UNTRUSTED and
cannot be cited as protection for any invariant. This script is the
CI-time guard that catches regressions to the T1.a baseline — if a
future commit strips headers from a panel file, this script fires.

Usage:
    .venv/bin/python scripts/test_currency_audit.py             # audit
    .venv/bin/python scripts/test_currency_audit.py --verbose   # show per-file detail
    .venv/bin/python scripts/test_currency_audit.py --json      # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _yaml_bootstrap import import_yaml

ZEUS_ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY_YAML = ZEUS_ROOT / "architecture" / "test_topology.yaml"
PANEL_KEY = "midstream_guardian_panel"

HEADER_PATTERNS = {
    "created": re.compile(r"^#\s*Created:\s*\d{4}-\d{2}-\d{2}"),
    "last_audited": re.compile(r"^#\s*Last reused/audited:\s*\d{4}-\d{2}-\d{2}"),
    "authority_basis": re.compile(r"^#\s*Authority basis:\s*\S"),
}


def _load_panel() -> list[str]:
    yaml = import_yaml()
    try:
        with TOPOLOGY_YAML.open() as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:  # D2 fix: clean error on malformed YAML
        raise SystemExit(f"{TOPOLOGY_YAML} failed to parse: {exc}")
    # T1.e D3 fix: panel is nested under `categories:` for consistency
    # with core_law_antibody/stale_obsolete/reverse_antibody_dangerous
    # and to inherit topology_doctor existing-file validation via
    # topology_doctor_test_checks.py + topology_doctor_registry_checks.py
    # which both iterate topology.get("categories").
    categories = data.get("categories") or {}
    panel = categories.get(PANEL_KEY)
    if not isinstance(panel, list):
        raise SystemExit(
            f"{TOPOLOGY_YAML} is missing the `categories.{PANEL_KEY}:` "
            "list — T1.e must register the midstream guardian panel "
            "before this audit can run."
        )
    # D1 fix: reject empty panel — prevents silent-pass regression if a
    # bad merge wipes the list.
    if len(panel) == 0:
        raise SystemExit(
            f"{TOPOLOGY_YAML} has `categories.{PANEL_KEY}:` set to an "
            "empty list. T1.e requires a non-empty panel — a zero-file "
            "audit greens trivially and defeats the T1.a guarantee."
        )
    return [str(p) for p in panel]


def _audit_file(path: Path) -> dict[str, bool]:
    if not path.exists():
        return {"exists": False}
    # Read the first 12 lines — headers live at file top per convention.
    lines = path.read_text().splitlines()[:12]
    result = {"exists": True}
    for name, pattern in HEADER_PATTERNS.items():
        result[name] = any(pattern.match(line) for line in lines)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--verbose", action="store_true", help="per-file detail")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    panel = _load_panel()
    audit = {rel: _audit_file(ZEUS_ROOT / rel) for rel in panel}
    missing = {
        rel: [k for k, v in result.items() if k != "exists" and not v]
        for rel, result in audit.items()
        if not result.get("exists")
        or not all(result[k] for k in HEADER_PATTERNS)
    }

    payload = {
        "panel_size": len(panel),
        "panel_file": str(TOPOLOGY_YAML.relative_to(ZEUS_ROOT)),
        "panel_key": PANEL_KEY,
        "missing_count": len(missing),
        "missing": missing,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        if not missing:
            print(f"OK: all {len(panel)} midstream guardian panel files carry dated provenance headers.")
        else:
            print(f"FAIL: {len(missing)} of {len(panel)} panel files missing dated provenance header markers:")
            for rel, gaps in missing.items():
                if not audit[rel].get("exists"):
                    print(f"  {rel}: FILE NOT FOUND")
                else:
                    print(f"  {rel}: missing {gaps}")
        if args.verbose and not args.json:
            print("\nFull audit:")
            for rel, result in audit.items():
                print(f"  {rel}: {result}")

    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
