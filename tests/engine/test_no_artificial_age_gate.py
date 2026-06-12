# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator no-caps law 2026-06-12 ("不允许设置任何的cap...消除
#   过度设计") + gate inventory /tmp/gate_overengineering_inventory.md D1/D2:
#   the 24h market-age buy_no ban (OPENING_INERTIA_MARKET_TOO_OLD, fired 0x in
#   7 live days) and the reactor market-channel reject (stopped firing
#   2026-06-06) were deleted; these antibodies pin the categories.
"""ANTIBODIES for the no-caps deletions.

1. Market AGE alone can never reject a candidate — the deleted age gate's
   identifiers must not return (EV admission belongs to trade-score /
   capital-efficiency; age-aware sizing belongs to Kelly's phase multiplier).
2. Market-channel event types that slip onto the EDLI queue still fail
   closed at the ADAPTER boundary scope gate (the surviving authority for
   the deleted reactor-side duplicate).
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def test_age_gate_identifiers_do_not_return():
    banned = ("OPENING_INERTIA_MARKET_TOO_OLD", "_opening_inertia_market_age_hours")
    hits = []
    for path in (REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in banned:
            if token in text:
                hits.append(f"{path.relative_to(REPO)}: {token}")
    assert not hits, (
        "the artificial market-age gate returned (operator no-caps law "
        f"2026-06-12): {hits}"
    )


def test_age_gate_enum_member_removed():
    from src.contracts.rejection_reasons import RejectionReason

    assert "OPENING_INERTIA_MARKET_TOO_OLD" not in {m.value for m in RejectionReason}


def test_market_channel_event_types_are_not_forecast_or_day0_lane():
    """The deleted reactor-side reject's surviving authority is the adapter
    FINAL BOUNDARY SCOPE GATE, which fail-closes every event type that is
    neither forecast-lane nor day0-lane (pinned by the existing day0
    boundary suite). Pin here that the market-channel types can never be
    classified into either lane — i.e. they always take the unknown-type
    fail-closed path."""
    # _DAY0_LANE_EVENT_TYPES is closure-local in the adapter factory; pin its
    # definition at source level (single definition site).
    from pathlib import Path

    src = (REPO / "src/engine/event_reactor_adapter.py").read_text()
    import re

    m = re.search(r"_DAY0_LANE_EVENT_TYPES: frozenset\[str\] = frozenset\(\{([^}]*)\}\)", src)
    assert m, "day0 lane event-type set definition moved — update this pin"
    day0_types = {t.strip().strip('\'\"') for t in m.group(1).split(",") if t.strip()}
    for event_type in ("BOOK_SNAPSHOT", "BEST_BID_ASK_CHANGED", "NEW_MARKET_DISCOVERED"):
        assert event_type != "FORECAST_SNAPSHOT_READY"
        assert event_type not in day0_types, (
            f"{event_type} classified day0-lane — it would run the decision "
            "pipeline instead of the unknown-type fail-closed rejection"
        )
