# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §4 sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.F

"""Token-budget enforcement tests for route_function.render().

T0 (≤500 tok) and T1 (≤1000 tok) are enforced (PASS).
T2 (≤2000 tok) and T3 (≤4000 tok) were xfail in Phase 0.F; both now
reliably pass (XPASS confirmed), so markers removed and promoted to
enforced assertions in Phase 0.H cleanup batch.
"""

from __future__ import annotations

import pytest

try:
    import tiktoken  # type: ignore
    _enc = tiktoken.encoding_for_model("cl100k_base")

    def _len(s: str) -> int:
        return len(_enc.encode(s))
except Exception:
    # char/4 approximation fallback (matches route_function._token_count)
    def _len(s: str) -> int:  # type: ignore[misc]
        return len(s) // 4


from src.architecture.route_function import render, route


def test_route_card_t0_under_500_tokens():
    card = route(["src/execution/harvester.py"])
    rendered = render(card, tier=0)
    tok = _len(rendered)
    assert tok <= 500, f"T0 render is {tok} tokens (limit 500):\n{rendered}"


def test_route_card_t1_under_1000_tokens():
    card = route(["src/execution/harvester.py", "src/state/ledger.py"])
    rendered = render(card, tier=1)
    tok = _len(rendered)
    assert tok <= 1000, f"T1 render is {tok} tokens (limit 1000):\n{rendered}"


def test_route_card_t2_under_2000_tokens():
    card = route([
        "src/execution/harvester.py",
        "src/state/ledger.py",
        "src/calibration/store.py",
        "src/control/control_plane.py",
    ])
    rendered = render(card, tier=2)
    tok = _len(rendered)
    assert tok <= 2000, f"T2 render is {tok} tokens (limit 2000)"


def test_route_card_t3_under_4000_tokens():
    card = route([
        "src/execution/harvester.py",
        "src/state/ledger.py",
        "src/calibration/store.py",
        "src/control/control_plane.py",
        "src/execution/executor.py",
        "src/state/venue_command_repo.py",
    ])
    rendered = render(card, tier=3)
    tok = _len(rendered)
    assert tok <= 4000, f"T3 render is {tok} tokens (limit 4000)"


def test_route_card_capabilities_field():
    """Smoke test: harvester.py resolves to settlement_write capability."""
    card = route(["src/execution/harvester.py"])
    assert "settlement_write" in card.capabilities, (
        f"Expected 'settlement_write' in capabilities, got {card.capabilities}"
    )


def test_route_card_reversibility_truth_rewrite():
    """settlement_write is TRUTH_REWRITE severity."""
    card = route(["src/execution/harvester.py"])
    assert card.reversibility == "TRUTH_REWRITE", (
        f"Expected TRUTH_REWRITE, got {card.reversibility}"
    )
