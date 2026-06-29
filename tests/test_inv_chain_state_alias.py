# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md §A7.

"""A7 antibody: the two `ChainState` classes must be imported only by their
domain-specific aliases, never by the bare colliding name.

`src/contracts/semantic_types.py` (per-position visibility) is `VenueVisibilityStatus`;
`src/state/chain_state.py` (per-cycle snapshot completeness) is `ChainSnapshotCompleteness`.
A bare `import ChainState` is the wrong-class footgun the design forbids.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
# `from <mod> import ... ChainState ...` where ChainState is imported by its bare name.
_BARE_IMPORT_RE = re.compile(r"^\s*from\s+[\w.]+\s+import\s+[^\n]*\bChainState\b", re.MULTILINE)


def test_no_module_imports_the_bare_ChainState_name() -> None:
    offenders = []
    for py in _SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in _BARE_IMPORT_RE.finditer(text):
            line = m.group(0)
            # `import ChainSnapshotCompleteness`/`VenueVisibilityStatus` are fine.
            if re.search(r"\bChainState\b", re.sub(r"ChainS\w+|VenueVisibilityStatus", "", line)):
                offenders.append(f"{py.relative_to(_SRC.parent).as_posix()}: {line.strip()}")
    assert not offenders, (
        "Bare `import ChainState` is forbidden (wrong-class footgun) — import "
        "VenueVisibilityStatus (per-position) or ChainSnapshotCompleteness (per-cycle):\n"
        + "\n".join(offenders)
    )
