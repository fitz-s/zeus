"""Read-only current-product Day0 carriers shared by monitor, auction and JIT."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.contracts.exceptions import ObservationUnavailableError


def current_wrh_day0_observation_carrier(
    conn, *, city, target_date: str, metric: str, now: datetime,
    canonical_owner: bool = False,
):
    """A read-only adapter carrier for the qualified current resolver product.

    This is never persisted as a monotone event. SCOPE: this owned WRH
    city/day/metric. DRAIN: the current-product acquisition/repair path.
    RESET: a valid nonempty current snapshot. Unknown/EMPTY cannot resurrect
    an obsolete event; the probability adapter still proves its own source,
    carrier and action authority under its existing laws.
    """
    from src.config import settlement_source_type_for_city

    if city is None or settlement_source_type_for_city(city, target_date).lower() != "noaa":
        return None
    from src.data.daily_observation_writer import read_current_noaa_wrh_snapshot
    from src.data.replacement_forecast_current_target_plan import _latest_authorized_day0_fact
    from src.contracts.settlement_semantics import SettlementSemantics
    from src.events.day0_authority import (
        DAY0_LIVE_AUTHORITY_MATCHES,
        DAY0_PROVISIONAL_CURRENT_SNAPSHOT,
    )
    from src.events.opportunity_event import (
        Day0ExtremeUpdatedPayload,
        make_day0_extreme_updated_event,
    )

    owned, snapshot = read_current_noaa_wrh_snapshot(
        conn, city=city, target_date=str(target_date), as_of=now,
        _canonical_owner=canonical_owner,
    )
    if owned is False:
        return None
    extreme = snapshot.extreme(metric) if owned is True and snapshot is not None else None
    if extreme is None:
        raise ObservationUnavailableError("WRH_CURRENT_SNAPSHOT_UNAVAILABLE")
    fact = _latest_authorized_day0_fact(
        conn, city=str(city.name), target_date=str(target_date),
        temperature_metric=metric, decision_time=now, require_settlement_channel=True,
    )
    if not isinstance(fact, Mapping) or any((
        fact.get("source") != "current_wrh_product:" + snapshot.source,
        fact.get("raw_payload_sha256") != snapshot.response_sha256,
        fact.get("observation_available_at") != snapshot.received_at.isoformat(),
        fact.get("observed_extreme_native") != extreme.value,
        fact.get("station_id") != snapshot.station,
        fact.get("unit") != snapshot.unit,
    )):
        raise ObservationUnavailableError("WRH_CURRENT_SNAPSHOT_SUPERSEDED_DURING_READ")
    payload = Day0ExtremeUpdatedPayload(
        city=str(city.name), target_date=str(target_date), metric=metric,
        settlement_source=snapshot.source, station_id=snapshot.station,
        settlement_source_type="noaa",
        observation_time=str(fact["observation_time"]),
        observation_available_at=snapshot.received_at.isoformat(),
        raw_value=extreme.value,
        rounded_value=int(SettlementSemantics.for_city(city).round_single(extreme.value)),
        high_so_far=extreme.value if metric == "high" else None,
        low_so_far=extreme.value if metric == "low" else None,
        evidence_finality=DAY0_PROVISIONAL_CURRENT_SNAPSHOT,
        observation_availability_basis="canonical_current_product_receipt",
        observation_transport="held_monitor_current_wrh_view",
        raw_report_identity=snapshot.response_sha256,
        **DAY0_LIVE_AUTHORITY_MATCHES,
    )
    carrier = make_day0_extreme_updated_event(
        entity_key="|".join((payload.city, payload.target_date, metric, payload.station_id)),
        source="held_monitor_current_wrh_view", observed_at=payload.observation_time,
        received_at=snapshot.received_at.isoformat(), payload=payload,
        causal_snapshot_id="current_wrh_product:" + snapshot.response_sha256,
    )

    # Creation describes this read-only view, not a new source receipt. Neither
    # source availability nor the semantic carrier identity is renewed here.
    return replace(carrier, created_at=now.isoformat())


def current_wrh_probability_event(
    conn, event, *, decision_time: datetime, reprove_existing_day0: bool = False,
):
    """Bind a probability read to current WRH truth without rewriting its trigger.

    Future forecast families and sources without an owned current WRH product
    keep their existing event. A claimed but unavailable product raises before
    probability/cache consumption; it never resurrects an older scalar carrier.
    """
    if event.event_type not in {
        "FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING", "DAY0_EXTREME_UPDATED",
    }:
        return event
    raw_payload = getattr(event, "payload_json", None)
    if not isinstance(raw_payload, str):
        return event
    payload = json.loads(raw_payload)
    if not reprove_existing_day0 and event.event_type == "DAY0_EXTREME_UPDATED" and (
        payload.get("observation_transport") != "held_monitor_current_wrh_view"
        and not str(getattr(event, "causal_snapshot_id", "") or "").startswith("current_wrh_product:")
    ):
        # An already-typed Day0 event retains its own source/conditioning law.
        # This seam promotes forecast triggers for current-product ownership;
        # it does not install a WRH dependency on other qualified Day0 sources.
        return event
    from src.config import runtime_cities_by_name, settlement_source_type_for_city

    city = runtime_cities_by_name().get(str(payload.get("city") or ""))
    if city is None:
        return event
    target_date = str(payload.get("target_date") or "")
    metric = str(payload.get("metric") or "").lower()
    if not target_date or metric not in {"high", "low"}:
        return event
    if settlement_source_type_for_city(city, target_date).lower() != "noaa":
        return event
    if decision_time.tzinfo is None:
        raise ValueError("CURRENT_WRH_PROBABILITY_TIME_NAIVE")
    if date.fromisoformat(target_date) > decision_time.astimezone(ZoneInfo(city.timezone)).date():
        return event
    return current_wrh_day0_observation_carrier(
        conn, city=city, target_date=target_date, metric=metric, now=decision_time,
    ) or event


def current_wrh_probability_replay_event(
    conn, event, *, selected_at: datetime, decision_time: datetime,
):
    """Replay the selected cut only while its native current revision still owns it."""
    selected = current_wrh_probability_event(
        conn, event, decision_time=selected_at, reprove_existing_day0=True,
    )
    current = current_wrh_probability_event(
        conn, event, decision_time=decision_time, reprove_existing_day0=True,
    )
    if any(str(getattr(item, "causal_snapshot_id", "") or "").startswith("current_wrh_product:")
           for item in (selected, current)) and (
        selected.causal_snapshot_id != current.causal_snapshot_id
        or selected.payload_hash != current.payload_hash
    ):
        # SCOPE: this selected family/action only. DRAIN: the normal current
        # source wake rebuilds its probability and action receipt. RESET: a new
        # selection cut reproduces the same still-current native revision.
        raise ObservationUnavailableError("WRH_CURRENT_PROBABILITY_REVISION_SUPERSEDED")
    return selected
