# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: §4 of Copilot-review-system design
"""check_review_instruction_coverage — verify instruction file applyTo globs resolve.

Rules:
  1. Every applyTo pattern in .github/instructions/*.instructions.md must
     match at least one file in the repo (prevents stale/dead patterns).
  2. Every canonical money-path module listed in REQUIRED_COVERAGE must
     be matched by at least one instruction file's applyTo patterns.

Exit: 0 = pass, 1 = one or more violations.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
APPLY_TO_RE = re.compile(r'^applyTo\s*:\s*"?([^"\n]+)"?', re.MULTILINE)

REQUIRED_COVERAGE = [
    "src/engine/evaluator.py",
    "src/contracts/execution_price.py",
    "src/contracts/settlement_semantics.py",
    "src/execution",
    "src/venue",
    "src/state/db.py",
    "src/analysis/evidence_report.py",
    "src/backtest",
    "scripts/ci",
    "architecture/money_path_ci.yaml",
]


def parse_apply_to(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    fm = FRONTMATTER_RE.match(text)
    if not fm:
        return []
    m = APPLY_TO_RE.search(fm.group(1))
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


def pattern_resolves(root: Path, pattern: str) -> bool:
    """Return True if the glob pattern matches at least one path under root."""
    try:
        return any(True for _ in root.glob(pattern))
    except (ValueError, NotImplementedError):
        return False


def pattern_covers_module(pattern: str, module: str) -> bool:
    """Return True if the glob pattern could match the given module path."""
    # Direct prefix or substring match in the pattern
    module_base = module.rstrip("/")
    # Strip wildcards from pattern to get base path
    pat_base = re.split(r"[*\[{]", pattern)[0].rstrip("/")
    if not pat_base:
        return True  # wildcard-only pattern covers everything
    return module_base.startswith(pat_base) or pat_base.startswith(module_base)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).parent.parent.parent
    instruction_dir = root / ".github" / "instructions"

    instruction_files = list(instruction_dir.glob("*.instructions.md"))
    if not instruction_files:
        print("No instruction files found.")
        return 0

    violations: list[str] = []
    all_patterns: list[tuple[str, str]] = []

    for f in instruction_files:
        patterns = parse_apply_to(f)
        for pat in patterns:
            all_patterns.append((f.name, pat))
            if not pattern_resolves(root, pat):
                violations.append(
                    f"{f.name}: applyTo pattern {pat!r} matches no files in repo"
                )

    # Rule 2: required coverage
    all_raw_patterns = [pat for _, pat in all_patterns]
    for required in REQUIRED_COVERAGE:
        covered = any(
            pattern_covers_module(pat, required) for pat in all_raw_patterns
        )
        if not covered:
            violations.append(
                f"UNCOVERED: no instruction file covers {required!r}"
            )

    if violations:
        for v in violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\n{len(violations)} violation(s) found.", file=sys.stderr)
        return 1

    print(
        f"OK: {len(instruction_files)} instruction file(s), "
        f"{len(all_patterns)} pattern(s) validated."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
