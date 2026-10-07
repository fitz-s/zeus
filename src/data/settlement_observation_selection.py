# Created: 2026-09-29
# Last reused/audited: 2026-10-01 (absence proof v2: typed UNITS container)
"""The resolver's hierarchy, not a hierarchy of convenient weather mirrors.

The 48 NOAA descriptions captured for 2026-09-29 prescribe WRH, then WU
Daily Observations if NOAA is unavailable by 23:59 ET the following day.
A transport/credential failure never establishes that absence. Preserve the
successful empty-page witness in the fallback atom's existing provenance.

Absence authority is a versioned product-bound proof persisted WITH its
coverage row (``data_coverage.evidence_json``): station, product, view, unit,
date, request identity, response hash and receipt time. A reason-only row (the
pre-proof writer) authorizes nothing; it stays FAILED with an expired embargo,
so the observation catch-up re-fetches it and a valid empty product re-mints
the current proof.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
import json
import sqlite3
from zoneinfo import ZoneInfo

EMPTY_AFTER_DEADLINE = "SOURCE_CONFIRMED_EMPTY_AFTER_CONTRACT_DEADLINE"
RULE = "noaa_wrh_then_wu_next_day_2359_ET_v1"
# v1 was minted while a malformed top-level UNITS container parsed as "no
# label"; v1 rows stay as evidence but authorize nothing until re-fetched.
PROOF_VERSION = "noaa_wrh_absence_proof_v2"
PRODUCT = "weather.gov_wrh_timeseries"


def gamma_response_witness(response, *, started_at, received_at, request_params) -> dict | None:
    """Retain the normal response's decoded original bytes and immutable custody."""
    import base64
    import hashlib
    from src.data.wu_hourly_client import capture_entity
    from src.contracts.settlement_semantics import gamma_capture_identity
    capture = capture_entity(response, started_at=started_at, finished_at=received_at,
        request_url="https://gamma-api.polymarket.com/events", request_params=request_params,
        native_unit="per_market_contract")
    if capture.entity is None:
        return None
    witness = {
        "entity_bytes_b64": base64.b64encode(capture.entity).decode("ascii"),
        "entity_sha256": hashlib.sha256(capture.entity).hexdigest(),
        "capture_started_at_utc": capture.started_at,
        "capture_received_at_utc": capture.finished_at,
        "request_url": capture.request_url, "request_params": capture.request_params,
        "source_issued_at_utc": None,
    }
    witness["capture_identity_sha256"] = gamma_capture_identity(witness)
    return witness


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
    from src.state.data_coverage import DataTable, has_evidence_column, record_failed
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
    if not has_evidence_column(conn):
        # SCOPE: this city/date absence. DRAIN: the evidence_json migration.
        # RESET: the next valid empty product after it mints the proof.
        logging.getLogger(__name__).warning(
            "noaa_wrh confirmed empty %s/%s not minted: data_coverage.evidence_json "
            "missing (migration pending)", city.name, target_date,
        )
        return False
    proof = {
        "proof_version": PROOF_VERSION, "product": PRODUCT, "station_id": station,
        "page_view": city.settlement_page_view, "unit": product.unit,
        "unit_label": product.unit_label, "target_date": str(target_date),
        "request_url": request_url, "response_sha256": product.response_sha256,
        "received_at": now.astimezone(timezone.utc).isoformat(),
    }
    record_failed(conn, data_table=DataTable.OBSERVATIONS, city=city.name,
                  data_source="noaa_wrh_" + station.lower(), target_date=target_date,
                  reason=EMPTY_AFTER_DEADLINE, retry_after=retry_after,
                  evidence=json.dumps(proof, sort_keys=True, separators=(",", ":")))
    logging.getLogger(__name__).warning(
        "noaa_wrh confirmed empty %s/%s view=%s unit=%s response_sha256=%s url=%s",
        city.name, target_date, city.settlement_page_view, product.unit,
        product.response_sha256, request_url,
    )
    return True


def _proof_binds(proof, city, target_date, *, now) -> bool:
    """A current-version proof for exactly this station/view/unit/date."""
    try:
        received = datetime.fromisoformat(str(proof["received_at"]))
        digest = str(proof["response_sha256"])
        return (
            proof["proof_version"] == PROOF_VERSION
            and proof["product"] == PRODUCT
            and proof["station_id"] == city.wu_station.upper()
            and proof["page_view"] == city.settlement_page_view
            and proof["unit"] == city.settlement_unit
            and proof["target_date"] == str(target_date)
            and bool(proof["request_url"])
            and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
            and received.tzinfo is not None
            and fallback_deadline(target_date) <= received <= now
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def noaa_absence_witness(conn, city, target_date: str | date, *, as_of=None):
    now = as_of or datetime.now(timezone.utc)
    deadline = fallback_deadline(target_date)
    if now.tzinfo is None or now < deadline:
        return None
    from src.state.data_coverage import _coverage_table_ref
    table = _coverage_table_ref(conn)
    try:
        # A table without evidence_json raises here: no proof, no witness.
        row = conn.execute(
            f"SELECT reason,fetched_at,evidence_json FROM {table} WHERE data_table='observations' "
            "AND city=? AND data_source=? AND target_date=? AND sub_key='' "
            "AND reason=? ORDER BY fetched_at DESC LIMIT 1",
            (city.name, "noaa_wrh_" + city.wu_station.lower(), str(target_date), EMPTY_AFTER_DEADLINE),
        ).fetchone()
        if row is None or row[2] is None:
            return None  # reason-only legacy row: revalidate by re-fetch first
        seen = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        if seen.tzinfo is None or not deadline <= seen <= now:
            return None
        proof = json.loads(row[2])
    except (sqlite3.Error, ValueError, TypeError):
        return None
    if not isinstance(proof, dict) or not _proof_binds(proof, city, target_date, now=now):
        return None
    return {"rule": RULE, "primary_source": "noaa_wrh_" + city.wu_station.lower(),
            "station_id": city.wu_station.upper(), "target_date": str(target_date),
            "page_view": city.settlement_page_view, "absence_basis": row[0],
            "absence_observed_at": seen.isoformat(), "fallback_deadline": deadline.isoformat(),
            "absence_proof": proof}


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
                        and seen.tzinfo is not None and fallback_deadline(target_date) <= seen <= now
                        and _proof_binds(witness["absence_proof"], city, target_date, now=now)):
                    witness = None
            except (KeyError, IndexError, TypeError, ValueError):
                witness = None
        return (1, {**witness, "selected": "FALLBACK_WU"}) if witness else None
    if source_type == "wu_icao" and (name == "wu_icao_history" or name.startswith("wu_icao_history_")):
        return 0, {"selected": "PRIMARY_WU"}
    if source_type == "hko" and (name == "hko_daily_api" or name.startswith("hko_daily_api_")):
        def original(field):
            try:
                return row[field] if row is not None else None
            except (KeyError, IndexError, TypeError):
                return None
        metadata = original(f"{metric}_provenance_metadata")
        try:
            parsed = json.loads(metadata) if isinstance(metadata, str) else metadata
            entity = parsed.get("source_entity") if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            entity = None
        # Selection names the product, never publication eligibility. Keep the
        # metric's original provenance/entity/clocks without borrowing its twin.
        return 0, {
            "selected": "PRIMARY_HKO_DAILY_EXTRACT", "source_grade": "UNKNOWN",
            "city": city.name, "target_date": str(target_date),
            "temperature_metric": metric, "source": source,
            "station_id": original("station_id"),
            "source_entity": entity,
            "provenance_metadata": metadata,
            "source_issued_at": (entity.get("source_issued_at_utc")
                                 if isinstance(entity, dict) else None),
            "fetched_at": original("fetched_at"),
        }
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
