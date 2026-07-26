# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: 7-day production block-event audit -- one stuck EDLI order
#   was blocking new-entry BUY admission for every family (32,763 blocking
#   instances, 20.97h/7d). This narrows the adapter-level gate
#   (event_bound_live_adapter_from_trade_conn's entry_submit_block_reason
#   check) to per-family.
"""Adapter-level per-family BUY entry block.

``event_bound_live_adapter_from_trade_conn`` gained a new
``entry_submit_family_block_reasons: Mapping[str, str]`` parameter alongside
the pre-existing (still-global) ``entry_submit_block_reason``. These tests
confirm: a BUY event whose own weather family_id (derived from its payload
city/target_date/metric via the same ``weather_family_id`` identity persisted
on SubmitPlanBuilt) is in the blocked map is refused; an unrelated family's
BUY event is NOT blocked by that map; and a SELL/exit candidate keeps its
pre-existing bypass ahead of both block checks.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import src.engine.event_reactor_adapter as era
from src.events.candidate_binding import weather_family_id
from src.events.opportunity_event import OpportunityEvent

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

FAMILY_A = weather_family_id(city="Dallas", target_date="2026-07-25", metric="high")
FAMILY_B = weather_family_id(city="Miami", target_date="2026-07-25", metric="low")


def _make_event(*, city: str, target_date: str, metric: str, event_id: str = "evt-1") -> OpportunityEvent:
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": metric,
    }
    return OpportunityEvent(
        event_id=event_id,
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}:{target_date}:{metric}",
        source="test",
        observed_at=NOW.isoformat(),
        available_at=NOW.isoformat(),
        received_at=NOW.isoformat(),
        causal_snapshot_id="snap-1",
        payload_hash="payload-hash",
        idempotency_key="idem-1",
        priority=0,
        expires_at=None,
        payload_json=json.dumps(payload),
        schema_version=1,
        created_at=NOW.isoformat(),
    )


def _make_adapter(*, entry_submit_block_reason=None, entry_submit_family_block_reasons=None):
    trade_conn = sqlite3.connect(":memory:")
    return era.event_bound_live_adapter_from_trade_conn(
        trade_conn,
        get_current_level=lambda: era.RiskLevel.GREEN,
        auction_capital_authority=SimpleNamespace(),
        entry_submit_block_reason=entry_submit_block_reason,
        entry_submit_family_block_reasons=entry_submit_family_block_reasons,
    )


def test_family_block_reasons_blocks_matching_family_buy_event():
    adapter = _make_adapter(
        entry_submit_family_block_reasons={
            FAMILY_A: "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1"
        }
    )
    event = _make_event(city="Dallas", target_date="2026-07-25", metric="high")

    receipt = adapter(event, NOW)

    assert receipt.submitted is False
    assert receipt.reason == (
        "LIVE_ENTRY_BLOCKED:entry_readiness_family:"
        "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1"
    )


def test_family_block_reasons_does_not_block_unrelated_family_buy_event():
    adapter = _make_adapter(
        entry_submit_family_block_reasons={
            FAMILY_A: "EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1",
        }
    )
    event = _make_event(city="Miami", target_date="2026-07-25", metric="low")
    assert weather_family_id(city="Miami", target_date="2026-07-25", metric="low") == FAMILY_B
    assert FAMILY_B not in {FAMILY_A}

    receipt = adapter(event, NOW)

    # Family B is not in the blocked map, so it must fall through this gate
    # entirely and hit the next boundary check (no executor_submit configured
    # in this minimal adapter) rather than the family-block reason.
    assert receipt.submitted is False
    assert receipt.reason == "EXECUTOR_BOUNDARY_MISSING"


def test_global_block_reason_still_blocks_every_family():
    adapter = _make_adapter(
        entry_submit_block_reason="entry_readiness:EDLI_STAGE_SOURCE_HEALTH_STALE:5s",
        entry_submit_family_block_reasons={},
    )
    event = _make_event(city="Miami", target_date="2026-07-25", metric="low")

    receipt = adapter(event, NOW)

    assert receipt.submitted is False
    assert receipt.reason == "LIVE_ENTRY_BLOCKED:entry_readiness:EDLI_STAGE_SOURCE_HEALTH_STALE:5s"


def test_global_sell_candidate_check_precedes_family_block_in_source_order():
    """SELL/exit candidates must keep bypassing BOTH block checks.

    The public per-event ``_submit(event, decision_time)`` seam never carries
    a ``global_actuation`` (only the batch/global-auction path does), so a
    SELL candidate cannot be driven through this narrow adapter surface
    without reconstructing that whole pipeline. This diff does not touch the
    SELL branch or its position: ``_global_sell_candidate(...)`` is still
    checked, and returns via ``_submit_global_sell(...)``, strictly before
    both ``entry_submit_block_reason`` and the new
    ``entry_submit_family_block_reasons`` check. Assert that ordering
    directly from source so a future edit that reorders these checks fails
    this test.
    """
    import inspect

    source = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    sell_at = source.index("_global_sell_candidate(global_actuation) is not None")
    global_block_at = source.index("if entry_submit_block_reason is not None:")
    family_block_at = source.index("if entry_submit_family_block_reasons:")

    assert sell_at < global_block_at < family_block_at
