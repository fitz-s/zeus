# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: docs/plans/2026-05-27-chain-local-refactor-part2-findings.md (Finding B2)
"""Antibody invariants: production code MUST NOT import the bare `ChainState` name.

Finding B2 (P2, Part-2 audit 2026-05-27): two enum classes named
`ChainState` live in the repo —
  - src/state/chain_state.py.ChainState         (per-cycle snapshot completeness)
  - src/contracts/semantic_types.py.ChainState  (per-position venue visibility)
They represent different real-world objects but share the Python class
name, so any production code importing the bare name is ambiguous to
readers and one wrong-module import away from a silent branch miss.

PR B (in PR #347) shipped domain-specific aliases:
  ChainSnapshotCompleteness = ChainState  # in src/state/chain_state.py
  VenueVisibilityStatus     = ChainState  # in src/contracts/semantic_types.py

PR B2 (Part-2 audit, this branch) completes the rename by:
  1. Migrating all production imports to the domain-specific names.
  2. Adding this static test to forbid future regressions.

The test scans src/ for `import` statements pulling the bare `ChainState`
symbol from either module. It allows the aliases themselves and any
import of `ChainState` from outside those two modules (defensive — no
such third home exists, but if one is added later for a third domain it
should be allowed there).

Tests directory is intentionally NOT scanned: tests may import the
legacy alias to verify the alias still exists.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

# Modules that DEFINE the legacy `ChainState` name. New code must import
# the domain-specific alias from these modules instead.
BANNED_SOURCES = (
    "src.state.chain_state",
    "src.contracts.semantic_types",
)

# The alias module that is allowed to keep the bare name (for declaring it).
ALLOWED_PATHS = {
    SRC_ROOT / "state" / "chain_state.py",
    SRC_ROOT / "contracts" / "semantic_types.py",
}


def _collect_chain_state_imports(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, module) for any `from <module> import ... ChainState ...`
    statement in `path` where module is one of BANNED_SOURCES."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module:
            continue
        if node.module not in BANNED_SOURCES:
            continue
        for alias in node.names:
            if alias.name == "ChainState":
                hits.append((node.lineno, node.module))
    return hits


def test_no_production_file_imports_bare_chain_state() -> None:
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if path in ALLOWED_PATHS:
            continue
        for lineno, module in _collect_chain_state_imports(path):
            rel = path.relative_to(REPO_ROOT)
            violations.append(
                f"{rel}:{lineno}: `from {module} import ChainState` — use "
                "`ChainSnapshotCompleteness` (from src.state.chain_state) or "
                "`VenueVisibilityStatus` (from src.contracts.semantic_types) instead"
            )

    assert not violations, (
        "Production code must import the domain-specific aliases "
        "(`ChainSnapshotCompleteness` / `VenueVisibilityStatus`), not the bare "
        "`ChainState` symbol — see Finding B2 in the Part-2 audit. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_aliases_remain_available() -> None:
    """Sanity: the two aliases must still resolve to the same underlying classes."""
    from src.contracts.semantic_types import ChainState as VisibilityClass
    from src.contracts.semantic_types import VenueVisibilityStatus
    from src.state.chain_state import ChainSnapshotCompleteness
    from src.state.chain_state import ChainState as SnapshotClass

    assert VenueVisibilityStatus is VisibilityClass
    assert ChainSnapshotCompleteness is SnapshotClass
    # Two distinct domain types — must remain different Python classes.
    assert VenueVisibilityStatus is not ChainSnapshotCompleteness
