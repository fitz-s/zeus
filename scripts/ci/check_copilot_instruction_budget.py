# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: §3 of Copilot-review-system design; GitHub Copilot 4000-char limit
"""check_copilot_instruction_budget — CI lint for Copilot instruction files.

Rules enforced:
  1. Every .instructions.md file must be ≤ CHAR_BUDGET characters.
  2. Every .instructions.md file (except copilot-instructions.md itself)
     that lives under .github/instructions/ must have an `applyTo`
     frontmatter field.
  3. No vague review phrases that produce zero-signal findings.

Exit codes: 0 = pass, 1 = one or more violations.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CHAR_BUDGET = 3600

VAGUE_PHRASES = [
    "ensure proper",
    "make sure",
    "be careful",
    "you should",
    r"\bconsider\b",
    "it is important",
    "in general",
    "as needed",
]

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
APPLY_TO_RE = re.compile(r"^applyTo\s*:", re.MULTILINE)


def check_file(path: Path) -> list[str]:
    violations: list[str] = []
    text = path.read_text(encoding="utf-8")
    size = len(text)

    if size > CHAR_BUDGET:
        violations.append(
            f"{path}: {size} chars exceeds budget of {CHAR_BUDGET}"
        )

    # applyTo required for path-specific files (not the root copilot-instructions.md)
    if path.name != "copilot-instructions.md":
        fm_match = FRONTMATTER_RE.match(text)
        if not fm_match:
            violations.append(f"{path}: missing YAML frontmatter block (--- ... ---)")
        elif not APPLY_TO_RE.search(fm_match.group(1)):
            violations.append(f"{path}: frontmatter missing `applyTo:` field")

    text_lower = text.lower()
    for phrase in VAGUE_PHRASES:
        if phrase.startswith(r"\b"):
            # word-boundary regex phrase
            if re.search(phrase, text_lower):
                violations.append(f"{path}: vague phrase found: {phrase!r}")
        elif phrase in text_lower:
            violations.append(f"{path}: vague phrase found: {phrase!r}")

    return violations


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="Instruction files to check. Defaults to .github/ scan.",
    )
    args = parser.parse_args(argv)

    if args.paths:
        files = [Path(p) for p in args.paths]
    else:
        root = Path(__file__).parent.parent.parent
        files = list(root.glob(".github/copilot-instructions.md")) + list(
            root.glob(".github/instructions/*.instructions.md")
        )

    if not files:
        print("No instruction files found — nothing to check.")
        return 0

    all_violations: list[str] = []
    for f in files:
        all_violations.extend(check_file(f))

    if all_violations:
        for v in all_violations:
            print(f"FAIL: {v}", file=sys.stderr)
        print(f"\n{len(all_violations)} violation(s) found.", file=sys.stderr)
        return 1

    print(f"OK: {len(files)} instruction file(s) passed budget check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
