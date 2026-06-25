# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator green-light 2026-06-10 item B (remaining-day
#   pricing + persist-the-hourly-vector option from the day0 first-principles
#   review §6.1/§6.3). INV-37: all writes go to zeus-forecasts.db under
#   db_writer_lock(LIVE); reads are mode=ro.
"""Day0 high-res hourly forecast vectors: persist + remaining-day extremes.

Why
---
The day0 entry lane priced P(bin) from the FULL-DAY forecast distribution
masked by the running extreme — not P(remaining-day excursion | now). The
review (2026-06-10 §2.4) classified that DEVIATES: post-peak it overprices
bins above the running max. The data needed to fix it (hourly curves from the
high-res models icon_d2 / arome HD / UKMO UKV 2km / NCEP NBM) was being
FETCHED and then reduced to a single daily extremum (raw_model_forecasts).
This module persists the bounded hourly vector so the day0 q can condition on
hours AFTER now.

Bounded by design
-----------------
- Only day0-relevant cities x in-domain regional models (polygon gate reused
  from src/forecast/model_selection.regional_eligible, lead 0).
- Only ~2 forecast days of hours per row; retention prunes rows older than
  DAY0_VECTOR_RETENTION_DAYS (default 3) on every write pass.
- Refresh throttled to once per DEFAULT_REFRESH_INTERVAL_S per process.

Provenance: every row carries source identity (provider/model/endpoint/
request hash), capture clock, and the model run identity open-meteo exposes.
Temperatures are ALWAYS degC in storage (the C/F unit-mix antibody from the
bayes_precision_fusion lane: convert at the consumption seam, never store mixed units).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

UTC = timezone.utc

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: High-res intraday models for the day0 remaining-day distribution
#: (operator charge #2: icon_d2 ~2km, arome HD, UKMO UKV 2km, NCEP NBM CONUS).
#: Each is domain-gated via config/model_domain_polygons.yaml.
DAY0_HOURLY_MODELS: tuple[str, ...] = (
    "icon_d2",
    "meteofrance_arome_france_hd",
    "ukmo_uk_deterministic_2km",
    "ncep_nbm_conus",
)
GLOBAL_DAY0_HOURLY_FALLBACK_MODELS: tuple[str, ...] = ("ecmwf_ifs",)

DAY0_VECTOR_RETENTION_DAYS = 3.0
DEFAULT_REFRESH_INTERVAL_S = 1800.0  # 30 min — high-res runs update hourly-ish
DEFAULT_FETCH_TIMEOUT_S = 4.0
DEFAULT_REFRESH_BUDGET_S = 6.0
DEFAULT_REFRESH_MAX_CITIES = 3

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS day0_hourly_vectors (
    vector_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    timezone_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openmeteo',
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash <> ''),
    times_json TEXT NOT NULL,
    temps_c_json TEXT NOT NULL,
    source_run_meta_json TEXT
)
"""
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_day0_hourly_vectors_city_date "
    "ON day0_hourly_vectors(city, target_date, captured_at)"
)


@dataclass(frozen=True)
class Day0HourlyVector:
    model: str
    city: str
    target_date: str
    timezone_name: str
    captured_at: str
    times: tuple[str, ...]       # ISO local timestamps as served (city timezone)
    temps_c: tuple[float, ...]   # ALWAYS degC


def in_domain_models_for_city(city: Any, *, models: Iterable[str] = DAY0_HOURLY_MODELS) -> list[str]:
    """Polygon-gated model list for a city (lead 0). Fail-soft to [] on gate errors."""
    try:
        from src.forecast.model_selection import load_domain_polygons, regional_eligible

        polygons = load_domain_polygons()
        lat = float(getattr(city, "lat"))
        lon = float(getattr(city, "lon"))
        return [
            model
            for model in models
            if regional_eligible(model, lat=lat, lon=lon, lead_days=0, polygons=polygons)
        ]
    except Exception as exc:  # noqa: BLE001 — gating failure means no vectors, never a crash
        logger.warning(
            "DAY0_HOURLY_VECTORS_DOMAIN_GATE_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return []


def day0_hourly_models_for_city(city: Any) -> list[str]:
    """Live Day0 remaining-day hourly model set for a city.

    Regional high-resolution models are preferred when the domain gate admits at least one. Global
    ECMWF IFS 9km is the universal fallback, matching the replacement forecast anchor source already
    used by the live probability chain. Without this fallback, cities outside the regional polygons
    enter the Day0 evaluator and then fail at ``DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE`` forever.
    """

    regional = in_domain_models_for_city(city)
    if regional:
        return regional
    return list(GLOBAL_DAY0_HOURLY_FALLBACK_MODELS)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE_DDL)
    conn.execute(_INDEX_DDL)


def _vector_id(model: str, city: str, target_date: str, captured_at: str) -> str:
    canonical = f"d0hv|{model}|{city}|{target_date}|{captured_at}"
    return "d0hv" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def build_request_hash(
    *,
    endpoint: str,
    params: dict,
    models: list[str],
    captured_at: str,
    payload: object,
) -> str:
    """Replayable provenance identity for one hourly-vector capture
    (PR#404 P1): canonicalized request params + endpoint + model list +
    captured_at bucket + response payload hash. A persisted vector row can
    always answer 'which exact request and response produced you'."""
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    canonical = "|".join((
        "d0hv_req_v1", endpoint, canonical_params, ",".join(sorted(models)),
        str(captured_at)[:16],  # minute bucket: idempotent within a capture pass
        payload_hash,
    ))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch_day0_hourly_vectors(
    city: Any,
    *,
    models: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
) -> tuple[list[Day0HourlyVector], str]:
    """Fetch the freshest hourly temperature curves for in-domain high-res models.

    One open-meteo forecast-API call per city (all models batched). degC
    forced. Returns (vectors, request_hash) — the hash is the replayable
    provenance identity persisted with every row (PR#404 P1: empty provenance
    identity is not acceptable for q-construction inputs). Fail-soft:
    ([], "") on any transport/shape error.
    """
    from src.data.openmeteo_client import fetch

    chosen = models if models is not None else day0_hourly_models_for_city(city)
    if not chosen:
        return [], ""
    captured_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    params = {
        "latitude": float(getattr(city, "lat")),
        "longitude": float(getattr(city, "lon")),
        "hourly": "temperature_2m",
        "models": ",".join(chosen),
        "temperature_unit": "celsius",  # storage law: degC ALWAYS
        "timezone": str(getattr(city, "timezone")),
        "forecast_days": 2,
    }
    try:
        payload = fetch(
            OPENMETEO_FORECAST_URL,
            params,
            endpoint_label=f"day0_hourly_{getattr(city, 'name', '?')}",
            timeout=max(0.5, float(timeout_s)),
            max_retries=1,
            backoff_sec=0.0,
            fast_fail_429=True,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft lane
        logger.warning(
            "DAY0_HOURLY_VECTORS_FETCH_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return [], ""
    request_hash = build_request_hash(
        endpoint=OPENMETEO_FORECAST_URL, params=params, models=chosen,
        captured_at=captured_at, payload=payload,
    )
    return (
        parse_openmeteo_hourly_payload(
            payload, city=city, models=chosen, captured_at=captured_at,
        ),
        request_hash,
    )


def parse_openmeteo_hourly_payload(
    payload: object,
    *,
    city: Any,
    models: list[str],
    captured_at: str,
) -> list[Day0HourlyVector]:
    """Parse a (possibly multi-model) open-meteo hourly payload.

    Multi-model requests return either a list of per-model dicts or a single
    dict with suffixed keys (temperature_2m_<model>). Both shapes handled;
    target_date is stamped per-vector at read time (the vector spans 2 days).
    """
    tz_name = str(getattr(city, "timezone"))
    city_name = str(getattr(city, "name", "") or "")

    def _vector_from(hourly: dict, model: str, temp_key: str) -> Optional[Day0HourlyVector]:
        times = hourly.get("time")
        temps = hourly.get(temp_key)
        if not isinstance(times, (list, tuple)) or not isinstance(temps, (list, tuple)):
            return None
        pairs = [
            (str(t), float(v))
            for t, v in zip(times, temps)
            if v is not None and isinstance(v, (int, float))
        ]
        if not pairs:
            return None
        return Day0HourlyVector(
            model=model,
            city=city_name,
            target_date="",  # stamped per consumption window
            timezone_name=tz_name,
            captured_at=captured_at,
            times=tuple(t for t, _ in pairs),
            temps_c=tuple(v for _, v in pairs),
        )

    out: list[Day0HourlyVector] = []
    if isinstance(payload, list):
        for model, entry in zip(models, payload):
            if isinstance(entry, dict) and isinstance(entry.get("hourly"), dict):
                vector = _vector_from(entry["hourly"], model, "temperature_2m")
                if vector is not None:
                    out.append(vector)
        return out
    if isinstance(payload, dict) and isinstance(payload.get("hourly"), dict):
        hourly = payload["hourly"]
        for model in models:
            vector = _vector_from(hourly, model, f"temperature_2m_{model}")
            if vector is None and len(models) == 1:
                # single-model responses may omit the model suffix
                vector = _vector_from(hourly, model, "temperature_2m")
            if vector is not None:
                out.append(vector)
    return out


def persist_day0_hourly_vectors(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    conn: Optional[sqlite3.Connection] = None,
    request_hash: str,
    endpoint: str = OPENMETEO_FORECAST_URL,
    retention_days: float = DAY0_VECTOR_RETENTION_DAYS,
    now: Optional[datetime] = None,
    lock_blocking: bool = True,
) -> int:
    """Persist vectors (idempotent on (model,city,date,captured_at)) + prune.

    conn=None -> zeus-forecasts.db under db_writer_lock(LIVE) per INV-37; the
    connection is OPENED INSIDE the flock (lock-order hygiene: connection-open
    contention stays under the same writer lock — Copilot PR#404 finding).

    request_hash is REQUIRED non-empty (PR#404 P1: rows feeding the
    remaining-day q must carry a replayable provenance identity; the table
    CHECK enforces the same on fresh DBs).

    ``now`` pins the retention-prune reference clock (the cutoff is
    ``now - retention_days``). Defaults to live wall-clock ``datetime.now(UTC)``
    so production behaviour is unchanged; tests inject it so a fixture with
    fixed captured_at timestamps is not pruned non-deterministically as real
    time advances past the retention window.
    """
    if not vectors:
        return 0
    if not str(request_hash or "").strip():
        raise ValueError(
            "persist_day0_hourly_vectors requires a non-empty request_hash "
            "(replayable provenance identity; see build_request_hash)"
        )
    own_conn = conn is None
    if own_conn:
        from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_ctx = db_writer_lock(
            ZEUS_FORECASTS_DB_PATH,
            WriteClass.LIVE,
            blocking=lock_blocking,
        )
    else:
        from contextlib import nullcontext

        lock_ctx = nullcontext()
    written = 0
    try:
        with lock_ctx:
            if own_conn:
                conn = get_forecasts_connection(write_class=WriteClass.LIVE)
            _ensure_schema(conn)
            for vector in vectors:
                row_id = _vector_id(vector.model, vector.city, target_date, vector.captured_at)
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO day0_hourly_vectors (
                        vector_id, model, city, target_date, timezone_name,
                        captured_at, provider, endpoint, request_hash,
                        times_json, temps_c_json, source_run_meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'openmeteo', ?, ?, ?, ?, NULL)
                    """,
                    (
                        row_id, vector.model, vector.city, target_date,
                        vector.timezone_name, vector.captured_at, endpoint,
                        request_hash, json.dumps(list(vector.times)),
                        json.dumps(list(vector.temps_c)),
                    ),
                )
                written += int(cur.rowcount or 0)
            prune_reference = (now or datetime.now(UTC)).astimezone(UTC)
            cutoff = prune_reference.timestamp() - retention_days * 86400.0
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
            conn.execute(
                "DELETE FROM day0_hourly_vectors WHERE captured_at < ?",
                (cutoff_iso,),
            )
            conn.commit()
    finally:
        # conn can be None when the connection-open itself failed inside the
        # flock — guard so the original exception is never masked (Copilot
        # PR#404 finding).
        if own_conn and conn is not None:
            conn.close()
    return written


def read_freshest_day0_hourly_vectors(
    *,
    city: str,
    target_date: str,
    max_age_hours: float = 3.0,
    now: Optional[datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[Day0HourlyVector]:
    """Freshest persisted vector per model for (city, target_date).

    Vectors older than max_age_hours are EXCLUDED (a stale high-res run must
    not masquerade as the current remaining-day distribution — fail-closed to
    the legacy full-day path instead).
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
    try:
        try:
            rows = conn.execute(
                """
                SELECT model, city, target_date, timezone_name, captured_at,
                       times_json, temps_c_json
                FROM day0_hourly_vectors
                WHERE city = ? AND target_date = ?
                ORDER BY captured_at DESC
                """,
                (str(city), str(target_date)),
            ).fetchall()
        except sqlite3.Error:
            return []
        freshest: dict[str, Day0HourlyVector] = {}
        for row in rows:
            model = str(row[0])
            if model in freshest:
                continue
            try:
                captured = datetime.fromisoformat(str(row[4]).replace("Z", "+00:00"))
                if captured.tzinfo is None:
                    continue
                age_hours = (moment - captured.astimezone(UTC)).total_seconds() / 3600.0
                if age_hours > float(max_age_hours) or age_hours < 0.0:
                    continue
                times = tuple(str(t) for t in json.loads(row[5]))
                temps = tuple(float(v) for v in json.loads(row[6]))
                if not times or len(times) != len(temps):
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            freshest[model] = Day0HourlyVector(
                model=model, city=str(row[1]), target_date=str(row[2]),
                timezone_name=str(row[3]), captured_at=str(row[4]),
                times=times, temps_c=temps,
            )
        return list(freshest.values())
    finally:
        if own_conn:
            conn.close()


def remaining_day_extremes_c(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    now: datetime,
    metric: str,
) -> list[float]:
    """Per-model remaining-day extreme (degC): hours of the local target day
    at/after ``now`` only. A vector with no remaining hours contributes nothing
    (the day is over for that model's grid — the obs floor owns the answer)."""
    if metric not in {"high", "low"}:
        raise ValueError(f"unsupported metric: {metric}")
    target = date.fromisoformat(str(target_date)[:10])
    out: list[float] = []
    for vector in vectors:
        try:
            tz = ZoneInfo(vector.timezone_name)
        except Exception:
            continue
        now_local = now.astimezone(tz)
        values: list[float] = []
        for raw_time, temp in zip(vector.times, vector.temps_c):
            try:
                local = datetime.fromisoformat(str(raw_time))
                if local.tzinfo is None:
                    local = local.replace(tzinfo=tz)
            except ValueError:
                continue
            if local.date() != target:
                continue
            if local < now_local:
                continue
            values.append(float(temp))
        if not values:
            continue
        out.append(max(values) if metric == "high" else min(values))
    return out


# ---------------------------------------------------------------------------
# Throttled refresh hook (wired from the day0 emit cycle; NO daemon restart
# needed for the schema — table is created on first write).
# ---------------------------------------------------------------------------

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_MONOTONIC: dict[str, float] = {}


def maybe_refresh_day0_hourly_vectors(
    cities: list[Any],
    *,
    decision_time: datetime,
    interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
    budget_s: float = DEFAULT_REFRESH_BUDGET_S,
    max_cities: int = DEFAULT_REFRESH_MAX_CITIES,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    persist_lock_blocking: bool = True,
) -> int:
    """Throttled per-city fetch+persist of the freshest high-res hourly curves.

    Only cities with at least one in-domain high-res model are fetched. One
    open-meteo call per city per interval. Fail-soft per city.
    """
    written = 0
    now_monotonic = time.monotonic()
    started_monotonic = now_monotonic
    checked = 0
    for city in cities:
        if checked >= max(0, int(max_cities)):
            break
        if budget_s > 0.0 and checked > 0 and (time.monotonic() - started_monotonic) >= budget_s:
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_BUDGET_EXHAUSTED checked=%d budget_s=%.3f",
                checked,
                budget_s,
            )
            break
        name = str(getattr(city, "name", "") or "")
        if not name:
            continue
        with _REFRESH_LOCK:
            last = _LAST_REFRESH_MONOTONIC.get(name, 0.0)
            if now_monotonic - last < float(interval_s):
                continue
            _LAST_REFRESH_MONOTONIC[name] = now_monotonic
        try:
            models = day0_hourly_models_for_city(city)
            if not models:
                continue
            checked += 1
            tz = ZoneInfo(str(getattr(city, "timezone")))
            target_date = decision_time.astimezone(tz).date().isoformat()
            try:
                vectors, request_hash = fetch_day0_hourly_vectors(
                    city, models=models, now=decision_time, timeout_s=timeout_s
                )
            except TypeError as exc:
                if "timeout_s" not in str(exc):
                    raise
                vectors, request_hash = fetch_day0_hourly_vectors(
                    city, models=models, now=decision_time
                )
            if vectors and request_hash:
                written += persist_day0_hourly_vectors(
                    vectors,
                    target_date=target_date,
                    request_hash=request_hash,
                    lock_blocking=persist_lock_blocking,
                )
        except Exception as exc:  # noqa: BLE001 — one city must not kill the pass
            if isinstance(exc, BlockingIOError):
                with _REFRESH_LOCK:
                    _LAST_REFRESH_MONOTONIC.pop(name, None)
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_FAILED city=%s exc=%s: %s",
                name, type(exc).__name__, exc,
            )
    return written
