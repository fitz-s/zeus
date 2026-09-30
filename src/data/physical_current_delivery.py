# Created: 2026-09-29
# Last reused/audited: 2026-09-29
"""Replay current-temperature debt from WORLD versus consumed FORECAST identity.

No source fetch, probability formula, execution action, or new truth store lives
here. A process-local cursor controls fairness only: restart rebuilds all debt
from existing observations and posterior provenance, never from an ack flag.
"""
from __future__ import annotations
from datetime import datetime, timezone
import logging
import threading
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

_LOG = logging.getLogger(__name__)
_CURSOR_LOCK = threading.Lock()
_CURSORS = [0, 0]


def current_temperature_priority_families() -> dict[tuple[str, str, str], int]:
    """Read held and resting exposure, including commands not yet projected.

    Two read-only handles, no ATTACH or cross-database write. Reuse the substrate
    owner's exact command -> snapshot -> condition -> family resolution rather
    than treating a missing position projection as proof of no resting order.
    """
    from contextlib import closing
    from src.data.replacement_forecast_seed_discovery import held_position_family_priorities
    from src.data.substrate_observer import _open_rest_scope_rows_for_refresh
    from src.state.db import get_trade_connection_read_only, get_forecasts_connection_read_only

    priorities = dict(held_position_family_priorities())
    try:
        with closing(get_trade_connection_read_only()) as trade:
            with closing(get_forecasts_connection_read_only()) as forecasts:
                rests = _open_rest_scope_rows_for_refresh(
                    trade, forecasts_conn=forecasts, strict=True,
                )
        for family, _condition_id in rests:
            priorities.setdefault(family, 1)
    except Exception as exc:
        # The next debt scan retries this read. Do not claim zero resting orders,
        # suppress other families, or stop serving a previously valid posterior.
        _LOG.warning("CURRENT_TEMPERATURE_REST_SCOPE_UNAVAILABLE error=%s", type(exc).__name__)
    return priorities

def current_temperature_delivery_scopes(
    cities: Sequence[Any], *, now: datetime,
    held: Mapping[tuple[str, str, str], int] | None = None,
) -> tuple[tuple[str, str, str], ...]:
    if now.tzinfo is None:
        raise ValueError("CURRENT_TEMPERATURE_DELIVERY_CLOCK_NAIVE")
    if held is None:
        held = current_temperature_priority_families()
    by_name = {city.name: city for city in cities}
    scopes = {
        (city.name, now.astimezone(ZoneInfo(city.timezone)).date().isoformat(), metric)
        for city in cities for metric in ("high", "low")
    }
    # Pending/resting entries also need repricing; an ended-day held scope must
    # not disappear merely because it is outside the new-entry calendar.
    scopes.update(scope for scope in held
                  if scope[0] in by_name and scope[2] in {"high", "low"})
    return tuple(sorted(scopes, key=lambda scope: (scope not in held, scope)))

def reconcile_current_temperature_delivery(
    cfg: dict[str, object], *, cities: Sequence[Any],
    now: datetime | None = None, max_scopes: int = 12,
) -> dict[str, object]:
    from src.data.replacement_forecast_production import _enqueue_fusion_upgrade_reseeds_if_needed
    now = now or datetime.now(timezone.utc)
    held = current_temperature_priority_families()
    all_scopes = current_temperature_delivery_scopes(cities, now=now, held=held)
    groups = ([s for s in all_scopes if s in held], [s for s in all_scopes if s not in held])
    selected = []
    limit = max(2, int(max_scopes))
    # Reserve progress for both money-at-risk and first-materialization scopes.
    # A persistently unmaterializable held family cannot starve other cities.
    with _CURSOR_LOCK:
        for index, group in enumerate(groups):
            if not group:
                continue
            count = min(len(group), (limit + 1) // 2 if all(groups) else limit)
            start = _CURSORS[index] % len(group)
            selected.extend(group[(start + offset) % len(group)] for offset in range(count))
            _CURSORS[index] = (start + count) % len(group)
    report = _enqueue_fusion_upgrade_reseeds_if_needed(
        cfg, scopes=tuple(selected), changed_sources=("day0_current_temperature_state",),
        computed_at=now, limit=len(selected) or 1,
    ) if selected else None
    return {"status": "CURRENT_TEMPERATURE_RECONCILED", "scopes_offered": len(selected),
            "total_scopes": len(all_scopes), "delivery": report}
