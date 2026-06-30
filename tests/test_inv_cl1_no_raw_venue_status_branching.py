# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: docs/operations/current/reports/state_vocabulary_canonical_redesign_2026-06-29.md
#   INV-CL-1 (single-ingress-normalizer invariant), consult round-2 §1.2 / §6.

"""INV-CL-1 antibody: raw venue-status strings may be branched on ONLY at ingress.

Every other module must consume the typed canonical vocabulary
(src/contracts/canonical_lifecycle.py) or typed predicates, never raw venue
status strings. This antibody pins the migration baseline: the set of files that
still branch on raw venue-status strings may only SHRINK as the cutover proceeds,
never grow. A NEW file introducing raw-status branching fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# Raw venue-status spellings that must be folded at ingress, not branched on.
# Narrowed to venue-jargon tokens with near-zero overload elsewhere (excludes
# overloaded words like LIVE/MATCHED/FILLED/PARTIAL/OPEN that appear in forecast
# completeness, *_ELIGIBLE, open-file, etc. — those are caught by the typed cutover,
# not this lexical guard).
_VENUE_STATUS_TOKENS = (
    "RESTING", "CANCELED", "PARTIALLY_FILLED", "PARTIALLY_MATCHED",
)
_BRANCH_RE = re.compile(
    r'(==|!=|\sin\s)\s*[\(\{]?"(' + "|".join(_VENUE_STATUS_TOKENS) + r')"'
)

# Files exempt from INV-CL-1: the normalizer itself, schema/CHECK definitions,
# and migration scripts are the SANCTIONED homes for raw status strings.
def _is_exempt(path: Path) -> bool:
    parts = path.as_posix()
    return (
        path.name == "canonical_lifecycle.py"
        or "/schema/" in parts
        or "migration" in parts
    )


# Migration baseline (2026-06-29): files that still branch on raw venue status.
# This set may only SHRINK. Each cutover step removes entries; none may be added.
_BASELINE_OFFENDERS = frozenset({
    "src/data/substrate_observer.py",
    "src/engine/cycle_runtime.py",
    "src/execution/command_recovery.py",
    "src/execution/executor.py",
    "src/execution/exit_lifecycle.py",
    "src/main.py",
})


def _current_offenders() -> set[str]:
    offenders: set[str] = set()
    for py in _SRC.rglob("*.py"):
        if _is_exempt(py):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if _BRANCH_RE.search(text):
            offenders.add(py.relative_to(_SRC.parent).as_posix())
    return offenders


def test_no_new_file_branches_on_raw_venue_status() -> None:
    current = _current_offenders()
    new_offenders = current - _BASELINE_OFFENDERS
    assert not new_offenders, (
        "INV-CL-1 violated: these files branch on raw venue-status strings but are "
        "not in the migration baseline. Use src.contracts.canonical_lifecycle "
        f"normalizers/typed predicates instead. New offenders: {sorted(new_offenders)}"
    )


def test_baseline_does_not_silently_grow_stale() -> None:
    # If a baseline file no longer offends (cutover removed it), tighten the
    # baseline so the antibody keeps ratcheting. This catches stale exemptions.
    current = _current_offenders()
    cleared = _BASELINE_OFFENDERS - current
    assert not cleared, (
        "These baseline files no longer branch on raw venue status — remove them "
        f"from _BASELINE_OFFENDERS to ratchet INV-CL-1 tighter: {sorted(cleared)}"
    )
