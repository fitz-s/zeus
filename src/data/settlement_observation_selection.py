# Created: 2026-09-29
# Last reused/audited: 2026-10-01
"""The resolver's hierarchy, not a hierarchy of convenient weather mirrors.

The 48 NOAA descriptions captured for 2026-09-29 prescribe WRH, then WU
Daily Observations if NOAA is unavailable by 23:59 ET the following day.
A transport/credential failure never establishes that absence. Preserve the
successful empty-page witness in the fallback atom's existing provenance.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

EMPTY_AFTER_DEADLINE = "SOURCE_CONFIRMED_EMPTY_AFTER_CONTRACT_DEADLINE"
RULE = "noaa_wrh_then_wu_next_day_2359_ET_v1"


def fallback_deadline(target_date: str | date) -> datetime:
    target = date.fromisoformat(str(target_date))
    return datetime.combine(target + timedelta(days=1), time(23, 59),
                            ZoneInfo("America/New_York")).astimezone(timezone.utc)


def record_confirmed_empty(conn, *, city, target_date, product, request_url: str,
                           retry_after: datetime, now: datetime | None = None) -> bool:
    """Mint the absence witness from one valid explicit-empty WRH product.

    The product must be a :class:`WrhProduct` for exactly this city's station,
    settlement unit and contract view, and must itself show no row for the day.
    Returns False (writes nothing) before the contract deadline or when the
    product does not prove the empty day. A parse failure never reaches here:
    malformed/incomplete bodies raise ``WrhPayloadInvalid`` in the parser.
    """
    from src.data.noaa_wrh_timeseries import WrhProduct
    from src.state.data_coverage import DataTable, record_failed
    import logging

    now = now or datetime.now(timezone.utc)
    station = str(city.wu_station or "").strip().upper()
    if (
        not isinstance(product, WrhProduct)
        or not station
        or product.station != station
        or product.unit != city.settlement_unit
        or now < fallback_deadline(target_date)
        or not product.confirms_empty(
            target_date_local=str(target_date), view=city.settlement_page_view
        )
    ):
        return False
    record_failed(conn, data_table=DataTable.OBSERVATIONS, city=city.name,
                  data_source="noaa_wrh_" + station.lower(), target_date=target_date,
                  reason=EMPTY_AFTER_DEADLINE, retry_after=retry_after)
    logging.getLogger(__name__).warning(
        "noaa_wrh confirmed empty %s/%s view=%s unit=%s response_sha256=%s url=%s",
        city.name, target_date, city.settlement_page_view, product.unit,
        product.response_sha256, request_url,
    )
    return True


def noaa_absence_witness(conn, city, target_date: str | date, *, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    deadline = fallback_deadline(target_date)
    if now.tzinfo is None or now < deadline:
        return None
    from src.state.data_coverage import _coverage_table_ref
    table = _coverage_table_ref(conn)
    try:
        row = conn.execute(
            f"SELECT reason,fetched_at FROM {table} WHERE data_table='observations' "
            "AND city=? AND data_source=? AND target_date=? AND sub_key='' "
            "AND reason=? ORDER BY fetched_at DESC LIMIT 1",
            (city.name, "noaa_wrh_" + city.wu_station.lower(), str(target_date), EMPTY_AFTER_DEADLINE),
        ).fetchone()
        if row is None:
            return None
        seen = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if seen.tzinfo is None or not deadline <= seen <= now:
            return None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return {"rule": RULE, "primary_source": "noaa_wrh_" + city.wu_station.lower(),
            "station_id": city.wu_station.upper(), "target_date": str(target_date),
            "page_view": city.settlement_page_view, "absence_basis": row[0],
            "absence_observed_at": seen.isoformat(), "fallback_deadline": deadline.isoformat()}


def observation_selection(conn, city, target_date, source: str, *, row=None, metric="high", as_of=None):
    """Return rank and authority witness, or None; no network and no writes."""
    from src.config import settlement_source_type_for_city
    source_type = settlement_source_type_for_city(city, target_date)
    name = str(source).strip().lower()
    if source_type == "noaa":
        if name == "noaa_wrh_" + city.wu_station.lower():
            return 0, {"rule": RULE, "selected": "PRIMARY_WRH", "page_view": city.settlement_page_view}
        if name != "wu_icao_history":
            return None  # Ogimet is neither the primary product nor the named fallback.
        witness = noaa_absence_witness(conn, city, target_date, as_of=as_of)
        if witness is None and row is not None:
            try:
                raw = row[f"{metric}_provenance_metadata"]
                witness = json.loads(raw).get("resolver_fallback") if isinstance(raw, str) else None
                seen = datetime.fromisoformat(witness["absence_observed_at"])
                now = as_of or datetime.now(timezone.utc)
                if not (witness["rule"] == RULE and witness["absence_basis"] == EMPTY_AFTER_DEADLINE
                        and witness["station_id"] == city.wu_station.upper()
                        and witness["target_date"] == str(target_date)
                        and witness["page_view"] == city.settlement_page_view
                        and seen.tzinfo is not None and fallback_deadline(target_date) <= seen <= now):
                    witness = None
            except (KeyError, IndexError, TypeError, ValueError):
                witness = None
        return (1, {**witness, "selected": "FALLBACK_WU"}) if witness else None
    if source_type == "wu_icao" and (name == "wu_icao_history" or name.startswith("wu_icao_history_")):
        return 0, {"selected": "PRIMARY_WU"}
    if source_type == "hko" and (name == "hko_daily_api" or name.startswith("hko_daily_api_")):
        return 0, {"selected": "PRIMARY_HKO_DAILY_EXTRACT"}
    return None


def collect_due_noaa_fallbacks(*, now=None, limit: int = 8):
    """Read debt, release read handles, fetch, then take a short write lease.

    The existing coverage/atom witnesses own recovery. No in-memory success flag
    can erase a failed fetch or committed-primary race. Called after daily ingest
    releases its coarse cross-DB writer flocks.
    """
    from src.config import cities_by_name
    from src.data.daily_obs_append import _fetch_wu_icao_daily_highs_lows, append_wu_city
    from src.state.data_coverage import _coverage_table_ref, coverage_row_status, CoverageStatus, DataTable
    from src.state.db import get_forecasts_connection_with_world_read_only, get_forecasts_connection_with_world

    now = now or datetime.now(timezone.utc)
    stats = {"offered": 0, "written": 0, "deferred": 0}
    with get_forecasts_connection_with_world_read_only() as conn:
        rows = conn.execute(
            f"SELECT city,target_date FROM {_coverage_table_ref(conn)} "
            "WHERE data_table='observations' AND reason=? "
            "ORDER BY fetched_at DESC", (EMPTY_AFTER_DEADLINE,),
        ).fetchall()
        due = []
        for name, target in rows:
            city = cities_by_name.get(name)
            if city is None or noaa_absence_witness(conn, city, target, as_of=now) is None:
                continue
            status = coverage_row_status(conn, data_table=DataTable.OBSERVATIONS,
                city=name, data_source="wu_icao_history", target_date=target)
            if status and (status[0] in {CoverageStatus.WRITTEN.value, CoverageStatus.LEGITIMATE_GAP.value}
                           or status[1] and str(status[1]) > now.isoformat()):
                continue
            due.append((city, target))
            if len(due) >= max(1, int(limit)):
                break
    for city, target in due:
        stats["offered"] += 1
        day = date.fromisoformat(target)
        fetched = _fetch_wu_icao_daily_highs_lows(city.wu_station, city.country_code,
            day, day, city.settlement_unit, city.timezone, require_station_identity=True)
        try:
            with get_forecasts_connection_with_world(write_class="bulk", blocking=False) as conn:
                witness = noaa_absence_witness(conn, city, target)
                if witness is None:
                    stats["deferred"] += 1
                    continue
                result = append_wu_city(city.name, [day], conn,
                    rebuild_run_id="contract-fallback-" + now.isoformat(),
                    resolver_fallback=witness, prefetched_result=fetched)
                stats["written"] += int(result["inserted"])
        except BlockingIOError:
            stats["deferred"] += 1
    return stats
