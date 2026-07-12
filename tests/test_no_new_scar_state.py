# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md §5 (INV-REVIEW-1);
#   consult round-3 (thread 6a42bc3d) — "review is not a phase/status".

"""INV-REVIEW-1 antibody: no NEW scar states in the position/chain lifecycle vocabularies.

Scar states (quarantine variants, size-mismatch, chain-absent-unattributed, confirmed-zero,
exit-pending-missing) are reconciliation-corner patches encoded as *lifecycle phases*. The ideal
(atlas §7C, §7D) moves that debt to a ReviewWorkItem log and keeps external truth = token balance.
Until that migration lands, this antibody freezes the current scar set in the three lifecycle enums
so it may only SHRINK — a NEW `*_quarantined / *_wiped / *_suspected / *_mismatch / ...` member fails
the build. Scoped to the LIFECYCLE enums only, so legitimate FAILED/RETRYING states in trade /
redemption / wrap vocabularies are untouched.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# The lifecycle vocabularies where scar states have historically accreted (file rel to src/, class).
_LIFECYCLE_ENUMS: list[tuple[str, str]] = [
    ("contracts/canonical_lifecycle.py", "PositionPhase"),
    ("contracts/semantic_types.py", "LifecycleState"),
    ("contracts/semantic_types.py", "ChainState"),  # per-position VenueVisibilityStatus
]

# A member NAME whose lowercased form matches this is a reconciliation scar, not a real phase.
_SCAR_RE = re.compile(
    r"quarantin|wiped|suspected|mismatch|unattributed|confirmed_zero|pending_missing",
    re.IGNORECASE,
)

# Frozen scar baseline (2026-06-30). May only SHRINK as the ReviewWorkItem migration retires them.
# T5 (docs/rebuild/quarantine_excision_2026-07-11.md, REPLACEMENT PHASE LAW):
# QUARANTINED / QUARANTINE_EXPIRED / ENTRY_AUTHORITY_QUARANTINED retired from
# all three lifecycle enums together (three-enum law) — ratcheted per the
# antibody's own instruction ("a scar retired from the enum... remove it from
# _SCAR_BASELINE").
_SCAR_BASELINE: dict[str, frozenset[str]] = {
    "PositionPhase": frozenset(),
    "LifecycleState": frozenset(),
    "ChainState": frozenset({
        "EXIT_PENDING_MISSING",
        "SIZE_MISMATCH_UNRESOLVED",
        "CHAIN_CONFIRMED_ZERO",
        "CHAIN_ABSENT_CONFIRMED_UNATTRIBUTED",
    }),
}


def _enum_member_names(rel: str, class_name: str) -> list[str]:
    tree = ast.parse((_SRC / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            names: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    names += [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    names.append(stmt.target.id)
            return names
    raise AssertionError(f"INV-REVIEW-1: enum {class_name} not found in {rel} (atlas anchor moved?)")


def _current_scars(rel: str, class_name: str) -> set[str]:
    return {n for n in _enum_member_names(rel, class_name) if _SCAR_RE.search(n)}


def test_no_new_scar_state_in_lifecycle_enums() -> None:
    new: dict[str, set[str]] = {}
    for rel, cls in _LIFECYCLE_ENUMS:
        extra = _current_scars(rel, cls) - _SCAR_BASELINE.get(cls, frozenset())
        if extra:
            new[cls] = extra
    assert not new, (
        "INV-REVIEW-1 violated: a NEW scar state was added to a lifecycle enum. Review debt "
        "(quarantine / size-mismatch / chain-absence) belongs in a ReviewWorkItem log, not a phase. "
        f"New scars: { {k: sorted(v) for k, v in new.items()} }"
    )


def test_scar_baseline_not_stale() -> None:
    """A scar retired from the enum (migration progress!) must tighten the baseline."""
    stale: dict[str, set[str]] = {}
    for rel, cls in _LIFECYCLE_ENUMS:
        gone = _SCAR_BASELINE.get(cls, frozenset()) - _current_scars(rel, cls)
        if gone:
            stale[cls] = gone
    assert not stale, (
        "INV-REVIEW-1 baseline stale — a scar was retired (good!); remove it from _SCAR_BASELINE "
        f"to ratchet: { {k: sorted(v) for k, v in stale.items()} }"
    )
