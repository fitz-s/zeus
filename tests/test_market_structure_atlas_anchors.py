# Created: 2026-06-30
# Last audited: 2026-06-30
# Authority basis: docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md
#   (the market-structure code atlas / 谱图). This antibody keeps that foundation map from rotting.

"""INV-ATLAS-1 antibody: the market-structure code atlas's owner anchors stay live.

The atlas (docs/.../market_structure_code_atlas_2026-06-30.md) is the foundation map: for every
stored mechanism and pure projection it names the SINGLE reducer / owner function and the ingress
normalizer, plus the runtime-cycle entry points. A map is only load-bearing if it stays true — if a
reducer is renamed, moved, or deleted without updating the atlas, the map silently lies.

This test pins each atlas-named owner to its file. Rename/move/delete an anchor → this test fails →
the atlas (and this list) must be updated in the same change. Cheap: a source grep for the definition,
no heavy imports (src/main.py wires the daemon; we do not import it).
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"

# Atlas Layer-1 reducers + ingress normalizers, Layer-2 projections, Layer-3 cycle entries.
# file (relative to src/) -> list of definitions that MUST exist (def/class name).
_ATLAS_ANCHORS: dict[str, list[str]] = {
    # Layer 1 — stored-mechanism reducers
    "execution/order_truth_reducer.py": ["class VenueOrderTruthReducer", "def reduce"],
    "state/venue_command_repo.py": ["def append_event"],
    "state/chain_state.py": ["def classify_chain_state"],
    "contracts/settlement_axes.py": [
        "def settlement_resolution_state_from_row",
        "def redemption_accounting_phase",
        "def economic_outcome_for_position",
    ],
    "contracts/settlement_outcome.py": ["def classify_settlement_outcome"],
    "state/settlement_writers.py": ["def write_settlement_with_era_provenance"],
    # Layer 1 — ingress normalizers (the ONLY sanctioned home for raw venue strings, INV-CL-1)
    "contracts/canonical_lifecycle.py": [
        "def normalize_venue_order_status",
        "def normalize_venue_trade_status",
        "def normalize_command_truth_state",
    ],
    # Layer 2 — pure projections
    "state/canonical_projections.py": ["def derive_position_phase", "def derive_exit_progress"],
    "state/portfolio.py": ["def evaluate_exit"],
    # Layer 3 — runtime-cycle entry points
    "main.py": [
        "def _edli_event_reactor_cycle",
        "def _exit_monitor_cycle",
        "def _settlement_skill_attribution_tick",
        "def _wrap_proceeds_same_tick",
    ],
}


def _defined(text: str, definition: str) -> bool:
    # Match at a line start (allowing indentation) so `def reduce` / `class X` is a real definition,
    # not a substring in a call or comment.
    return re.search(rf"(?m)^\s*{re.escape(definition)}\b", text) is not None


def test_atlas_owner_anchors_are_live() -> None:
    missing: dict[str, list[str]] = {}
    for rel, definitions in _ATLAS_ANCHORS.items():
        path = _SRC / rel
        if not path.exists():
            missing[rel] = ["<file missing>"]
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        gone = [d for d in definitions if not _defined(text, d)]
        if gone:
            missing[rel] = gone
    assert not missing, (
        "INV-ATLAS-1 violated: market-structure atlas anchors moved/renamed/deleted. Update "
        "docs/operations/current/reports/market_structure_code_atlas_2026-06-30.md AND this list "
        f"in the same change. Missing: { {k: v for k, v in missing.items()} }"
    )
