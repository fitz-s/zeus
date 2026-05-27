# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-position-model-refactor.md (Finding 10, PR F)
"""Antibody invariants: every "void on missing chain" doc passage MUST name
the CHAIN_EMPTY precondition.

Finding 10 (P3 doc drift): the older README + module-doc wording said
"Local but NOT on chain → VOID immediately". A future agent reading
that phrase as a complete rule could "simplify" chain_reconciliation to
restore the unsafe semantics — voiding live positions on a degraded API
snapshot. Static text-scan tests prevent the regression.

Scanned files:
  - README.md
  - docs/reference/zeus_domain_model.md
  - docs/reference/zeus_execution_lifecycle_reference.md
  - src/state/chain_reconciliation.py (module docstring + Rule-2 comment)

Rule: any line containing "void" (case-insensitive) within ±WINDOW lines
(see constant below — currently ±30, paragraph-scale not statement-scale)
of "NOT on chain" / "not on chain" MUST also have "CHAIN_EMPTY" or
"chain_empty" within the same window. Sites that intentionally describe
the void path itself must therefore reference the completeness gate.

Window sizing rationale: ±10 was too narrow (false positives on counter
increments inside void blocks); ±30 is paragraph-scale and catches drift
without over-firing on incidental `voided += 1` style counters. Update
the failure message + this docstring together if WINDOW changes.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SCANNED_FILES = (
    "README.md",
    "docs/reference/zeus_domain_model.md",
    "docs/reference/zeus_execution_lifecycle_reference.md",
    "src/state/chain_reconciliation.py",
)

# Phrases that indicate the "local missing from chain" surface.
ABSENCE_TRIGGERS = re.compile(
    r"NOT on chain|not on chain|missing from chain|absent from chain",
    flags=re.IGNORECASE,
)

# Phrases that, when paired with an absence trigger, must justify themselves
# with a CHAIN_EMPTY precondition.
VOID_TRIGGERS = re.compile(r"\bvoid", flags=re.IGNORECASE)

# Acceptable safety qualifier within the same line-window.
COMPLETENESS_OK = re.compile(r"CHAIN_EMPTY|chain_empty", flags=re.IGNORECASE)

WINDOW = 30  # ±N lines (paragraph-scale, not statement-scale)


def _violations_in(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if not (VOID_TRIGGERS.search(line) and ABSENCE_TRIGGERS.search(line)):
            # If "void" and an absence trigger are not on the SAME line, look
            # at the wider neighborhood for an absence trigger.
            if not VOID_TRIGGERS.search(line):
                continue
            lo = max(0, idx - WINDOW)
            hi = min(len(lines), idx + WINDOW + 1)
            window = "\n".join(lines[lo:hi])
            if not ABSENCE_TRIGGERS.search(window):
                continue
        # We have a void+absence pairing within the local window.
        lo = max(0, idx - WINDOW)
        hi = min(len(lines), idx + WINDOW + 1)
        window = "\n".join(lines[lo:hi])
        if not COMPLETENESS_OK.search(window):
            out.append((idx + 1, line.strip()))
    return out


def test_void_wording_always_names_chain_empty_precondition() -> None:
    violations: list[str] = []
    for rel_path in SCANNED_FILES:
        path = REPO_ROOT / rel_path
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, snippet in _violations_in(text):
            violations.append(f"{rel_path}:{lineno}: {snippet!r}")

    assert not violations, (
        "'void' near 'not on chain' must be qualified by CHAIN_EMPTY/chain_empty "
        f"within ±{WINDOW} lines (Finding 10 / PR F — prevent regression to unsafe "
        '"VOID immediately" wording that ignores degraded snapshots).\n'
        "Violations:\n  " + "\n  ".join(violations)
    )
