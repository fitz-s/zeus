#!/usr/bin/env python3
"""Review scope collector — groups changed files into Tier 0/1/2/3 per REVIEW.md §4.

Usage:
  python scripts/review_scope_collect.py path/to/file.py ...
  python scripts/review_scope_collect.py --base origin/main
  python scripts/review_scope_collect.py --base HEAD~1 --json

Exit codes:
  0 — no advisory failure (Tier-0 files have tests or no Tier-0 files changed)
  1 — advisory: Tier-0 files changed with no corresponding tests in the diff
      and no AI-review-scope note present.  Never hard-blocks; advisory only.

# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: REVIEW.md §4 (Tier 0/1/2/3 definitions)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Tier classification rules — verbatim from REVIEW.md §4
# Each rule is (tier, glob_pattern).  First match wins (lowest tier = highest risk).
# ---------------------------------------------------------------------------
TIER_RULES: list[tuple[int, str]] = [
    # Tier 0 — Live money / runtime safety / kill switch
    (0, "src/execution/*"),
    (0, "src/execution/**/*"),
    (0, "src/venue/*"),
    (0, "src/venue/**/*"),
    (0, "src/contracts/settlement_semantics.py"),
    (0, "src/contracts/execution_price.py"),
    (0, "src/contracts/venue_submission_envelope.py"),
    (0, "src/contracts/fx_classification.py"),
    (0, "src/state/lifecycle_manager.py"),
    (0, "src/state/chain_reconciliation.py"),
    (0, "src/state/db.py"),
    (0, "src/state/ledger.py"),
    (0, "src/state/projection.py"),
    (0, "src/state/collateral_ledger.py"),
    (0, "src/state/venue_command_repo.py"),
    (0, "src/state/readiness_repo.py"),
    (0, "src/riskguard/*"),
    (0, "src/riskguard/**/*"),
    (0, "src/control/*"),
    (0, "src/control/**/*"),
    (0, "src/supervisor_api/*"),
    (0, "src/supervisor_api/**/*"),
    (0, "src/main.py"),
    (0, "src/engine/cycle_runner.py"),
    (0, "src/engine/evaluator.py"),
    (0, "src/engine/monitor_refresh.py"),
    (0, "migrations/*"),
    (0, "migrations/**/*"),
    (0, "architecture/2026_04_02_architecture_kernel.sql"),
    (0, "maintenance_worker/core/validator.py"),
    (0, "maintenance_worker/core/apply_publisher.py"),
    (0, "scripts/topology_v_next/admission_engine.py"),
    (0, "scripts/topology_v_next/hard_safety_kernel.py"),
    (0, "bindings/zeus/safety_overrides.yaml"),

    # Tier 1 — Data / probability / persistence correctness
    (1, "src/calibration/*"),
    (1, "src/calibration/**/*"),
    (1, "src/signal/*"),
    (1, "src/signal/**/*"),
    (1, "src/strategy/*"),
    (1, "src/strategy/**/*"),
    (1, "src/data/*"),
    (1, "src/data/**/*"),
    (1, "src/ingest/*"),
    (1, "src/ingest/**/*"),
    (1, "src/contracts/calibration_bins.py"),
    (1, "src/contracts/edge_context.py"),
    (1, "src/contracts/epistemic_context.py"),
    (1, "src/contracts/vig_treatment.py"),
    (1, "src/contracts/reality_contract.py"),
    (1, "src/contracts/reality_contracts_loader.py"),
    (1, "src/contracts/reality_verifier.py"),
    (1, "src/contracts/provenance_registry.py"),
    (1, "src/oracle/*"),
    (1, "src/oracle/**/*"),
    (1, "src/observability/*"),
    (1, "src/observability/**/*"),
    (1, "src/types/*"),
    (1, "src/types/**/*"),
    (1, "src/runtime/*"),
    (1, "src/runtime/**/*"),
    (1, "src/risk_allocator/*"),
    (1, "src/risk_allocator/**/*"),
    (1, "src/analysis/*"),
    (1, "src/analysis/**/*"),
    (1, "src/backtest/*"),
    (1, "src/backtest/**/*"),
    (1, "src/state/portfolio.py"),
    (1, "src/state/portfolio_loader_policy.py"),
    (1, "src/state/decision_chain.py"),
    (1, "src/state/job_run_repo.py"),
    (1, "src/state/source_run_repo.py"),
    (1, "src/state/market_topology_repo.py"),

    # Tier 2 — Tests and validation
    (2, "tests/contracts/*"),
    (2, "tests/contracts/**/*"),
    (2, "tests/test_*invariant*.py"),
    (2, "tests/test_architecture_contracts.py"),
    (2, "tests/*"),
    (2, "tests/**/*"),

    # Tier 3 — Docs / instructions / agent surfaces
    (3, "AGENTS.md"),
    (3, "src/**/AGENTS.md"),
    (3, "docs/**/AGENTS.md"),
    (3, "tests/**/AGENTS.md"),
    (3, "architecture/**/AGENTS.md"),
    (3, ".agents/*"),
    (3, ".agents/**/*"),
    (3, ".claude/skills/**/*"),
    (3, ".claude/agents/**/*"),
    (3, ".claude/hooks/**/*"),
    (3, ".claude/settings.json"),
    (3, ".claude/CLAUDE.md"),
    (3, ".github/copilot-instructions.md"),
    (3, ".github/instructions/**/*"),
    (3, ".github/pull_request_template.md"),
    (3, ".github/workflows/*"),
    (3, ".github/workflows/**/*"),
    (3, "architecture/*"),
    (3, "architecture/**/*"),
    (3, "docs/authority/**/*"),
    (3, "docs/operations/current*.md"),
    (3, "docs/operations/current/**/*"),
    (3, "docs/reference/**/*"),
    (3, "docs/review/**/*"),
    (3, "REVIEW.md"),
    (3, "workspace_map.md"),
    (3, "docs/archive_registry.md"),
]

# Deprioritized / skip-list patterns — map to tier "skip"
# Skip-list directory prefixes (any path under these is skip=9).
SKIP_PREFIXES: list[str] = [
    ".claude/orchestrator/",
    ".claude/worktrees/",
    ".code-review-graph/",
    ".omc/",
    ".omx/",
    ".zeus/",
    ".zeus-githooks/",
    ".zpkt-cache/",
    "docs/archives/",
    "docs/artifacts/",
    "docs/reports/",
    "docs/operations/archive/",
    "docs/historical_evidence/",
    "logs/",
    "raw/",
    "state/",
    "__pycache__/",
]

# Skip-list fnmatch patterns for individual files.
SKIP_FILE_PATTERNS: list[str] = [
    "*.lock",
    ".DS_Store",
    "*.log",
    "*.pyc",
    ".gitleaks.toml",
    ".importlinter",
]

TIER_NAMES = {0: "Tier 0", 1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 9: "Skip"}
TIER_DESC = {
    0: "Live money / runtime safety / kill switch",
    1: "Data / probability / persistence correctness",
    2: "Tests and validation",
    3: "Docs / instructions / agent surfaces",
    9: "Deprioritized / skip",
}


def _matches_tier_rule(p: str, pat: str) -> bool:
    """fnmatch match with ** treated as 'any path segment(s)'."""
    if "**" in pat:
        # Split on ** and check prefix + suffix.
        parts = pat.split("**/")
        prefix = parts[0]
        if not p.startswith(prefix):
            return False
        # The remainder after the prefix: match trailing glob against basename or full tail.
        suffix_pat = parts[-1]
        tail = p[len(prefix):]
        # tail must match suffix_pat with standard fnmatch (single-level wildcard).
        return fnmatch(tail, suffix_pat) or fnmatch(tail.split("/")[-1], suffix_pat)
    return fnmatch(p, pat)


def classify(path: str) -> int:
    """Return tier (0-3) or 9 (skip) for a path."""
    p = path.lstrip("/")
    # Check skip prefixes (top-level directory-tree skips — exact prefix match).
    for prefix in SKIP_PREFIXES:
        if p.startswith(prefix):
            return 9
    # Check skip file patterns.
    basename = p.split("/")[-1]
    for pat in SKIP_FILE_PATTERNS:
        if fnmatch(basename, pat):
            return 9
    # Tier classification.
    for tier, pat in TIER_RULES:
        if _matches_tier_rule(p, pat):
            return tier
    # Fallback: anything in src/ that wasn't caught = Tier 1
    if p.startswith("src/"):
        return 1
    # Fallback: test files
    if p.startswith("tests/") or p.startswith("test_"):
        return 2
    # Default: treat as Tier 3
    return 3


def get_changed_files(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [f for f in result.stdout.splitlines() if f.strip()]


def is_test_file(path: str) -> bool:
    p = Path(path)
    return p.stem.startswith("test_") or p.parent.name == "tests" or "tests" in p.parts


def advisory_fail(tier0_files: list[str], all_files: set[str]) -> bool:
    """Return True if advisory-fail: Tier-0 touched, no test files in diff."""
    if not tier0_files:
        return False
    test_files_in_diff = [f for f in all_files if is_test_file(f)]
    return len(test_files_in_diff) == 0


def build_table(grouped: dict[int, list[str]]) -> str:
    lines = []
    for tier in sorted(grouped):
        name = TIER_NAMES[tier]
        desc = TIER_DESC[tier]
        files = grouped[tier]
        lines.append(f"\n{name} — {desc} ({len(files)} file(s))")
        lines.append("-" * 60)
        for f in files:
            lines.append(f"  {f}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Group changed files into Tier 0/1/2/3 per REVIEW.md §4."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Explicit list of changed files.",
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        help="Git ref to diff against (git diff --name-only <ref>...HEAD).",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit JSON instead of human table.",
    )
    args = parser.parse_args(argv)

    if args.base and args.files:
        parser.error("Specify either --base or file arguments, not both.")

    if args.base:
        try:
            files = get_changed_files(args.base)
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: git diff failed: {exc.stderr}", file=sys.stderr)
            return 2
    elif args.files:
        files = list(args.files)
    else:
        parser.error("Provide either file arguments or --base <ref>.")
        return 2  # unreachable but satisfies type checkers

    grouped: dict[int, list[str]] = {0: [], 1: [], 2: [], 3: [], 9: []}
    for f in files:
        tier = classify(f)
        grouped[tier].append(f)

    tier0 = grouped[0]
    advisory = advisory_fail(tier0, set(files))

    if args.json_output:
        out = {
            "summary": {
                "tier0": len(tier0),
                "tier1": len(grouped[1]),
                "tier2": len(grouped[2]),
                "tier3": len(grouped[3]),
                "skip": len(grouped[9]),
                "total": len(files),
                "advisory_fail": advisory,
            },
            "files": {
                "tier0": tier0,
                "tier1": grouped[1],
                "tier2": grouped[2],
                "tier3": grouped[3],
                "skip": grouped[9],
            },
        }
        print(json.dumps(out, indent=2))
    else:
        print(build_table(grouped))
        if advisory:
            print(
                "\nADVISORY: Tier-0 files changed with no test files in the diff. "
                "Add or update tests covering the changed runtime-safety paths.",
                file=sys.stderr,
            )
        else:
            print("\nOK: advisory check passed.")

    return 1 if advisory else 0


if __name__ == "__main__":
    sys.exit(main())
