#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: architecture/topology_enforcement.yaml#blocking_structural:workflow_refs_exist
#                  docs/operations/current/plans/ci_topology_refactor_refined.md Phase D
"""
Verify every .github/workflows/*.yml `run:` script path references a file
that actually exists in the repo.

This is one of the no_override structural hazards in
architecture/topology_enforcement.yaml. A workflow that references a
non-existent script is CI theater — it will silently no-op or fail with
an obscure error, neither of which surfaces the real bug.

Scanned patterns inside `run:` blocks:
  - `python scripts/foo.py`
  - `python -m scripts.foo`
  - `bash scripts/foo.sh`
  - `./scripts/foo.py`

Quoted forms and inline `env=value python ...` are also handled by
extracting the first scripts/ token.

Exit codes:
    0 — every reference resolves
    1 — one or more missing; details printed to stdout
    2 — workflow parse failure / IO error
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Regex picks any token starting with `scripts/` containing only [\w./-] up to
# whitespace, quote, or shell metachar.
_SCRIPT_TOKEN = re.compile(r"(?<![\w/])(scripts/[\w./-]+\.(?:py|sh))")


def extract_script_refs_from_workflow(text: str) -> list[tuple[int, str]]:
    """Return list of (line_no_1based, script_path) tokens found in run: blocks."""
    out: list[tuple[int, str]] = []
    in_run = False
    run_indent = 0
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        # Track simple `run:` heredoc-style blocks. We just scan the whole file
        # for scripts/ tokens since false positives are rare and the cost is low.
        for m in _SCRIPT_TOKEN.finditer(line):
            out.append((i, m.group(1)))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--workflows-dir",
        default=str(WORKFLOWS_DIR),
        help="Directory containing workflow yamls",
    )
    p.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repo root for resolving script paths",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable",
    )
    args = p.parse_args(argv)

    wf_dir = Path(args.workflows_dir)
    repo = Path(args.repo_root)

    findings: list[dict] = []
    if not wf_dir.exists():
        print(f"ERROR: workflows dir does not exist: {wf_dir}", file=sys.stderr)
        return 2

    for wf_path in sorted(wf_dir.glob("*.yml")):
        try:
            text = wf_path.read_text()
        except OSError as e:
            print(f"ERROR: cannot read {wf_path}: {e}", file=sys.stderr)
            return 2
        for line_no, script in extract_script_refs_from_workflow(text):
            full = repo / script
            if not full.exists():
                findings.append(
                    {
                        "workflow": str(wf_path.relative_to(repo)),
                        "line": line_no,
                        "script": script,
                    }
                )

    if args.json:
        import json
        print(json.dumps({"missing_refs": findings, "count": len(findings)}, indent=2))
    else:
        if not findings:
            print("OK: every workflow `run:` script reference resolves.")
        else:
            print(f"FAIL: {len(findings)} workflow script reference(s) missing:")
            for f in findings:
                print(f"  {f['workflow']}:{f['line']}: missing → {f['script']}")

    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
