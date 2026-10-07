# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (Implementation design: dispatch, evidence classes, identity); coordinator correction
#   2026-10-07 (B_A from the settlement page only).
"""Receipt-gated evidence and dispatch for the Day0 dense state-space carrier.

``dense_remaining_carrier`` is called by ``build_day0_remaining_probability_carrier`` after its
own input validation.  It returns the dense carrier only when all three hold:
  (a) the city and metric have fitted parameters and the target date is after their training data;
  (b) the dense channel has receipt-gated rows on the target local day, and the newest one is
      fresh;
  (c) every input is valid (unit C, intraday decision, forecast path and topology valid).
Otherwise it returns None and the caller runs the legacy operator with unchanged arguments.

Information set.  Evidence is admitted by receipt at tau, the first receipt of the current-state
print named by ``identity_inputs['current_path_state']`` (tau <= decision).  Every writer and
replayer of a Day0 carrier passes that state verbatim, so the dense carrier is a deterministic
function of it.  A replay at a later clock reproduces the persisted certificate.  A newer state
print is a new information set and a new identity.  Reads: FORECAST ``day0_hourly_vectors``
(captured_at <= tau) and WORLD ``observation_prints`` (fetched_at_utc <= tau), on a read-only
connection.  Pure computation runs outside any write lock.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import json
import logging
import math
import sqlite3
import threading
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from src.contracts.settlement_semantics import SettlementSemantics, settlement_preimage_offsets
from src.data import day0_dense_state_space as ds

UTC = timezone.utc
DAY0_DENSE_STATE_SPACE_OPERATOR = "dense_observation_state_space_extreme_v1"
FORECAST_MODEL = "ecmwf_ifs"
N_SAMPLES_MIN = 1
logger = logging.getLogger(__name__)
_CACHE: "OrderedDict[str, dict[str, object]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_SIZE = 256


class DenseUnavailable(Exception):
    """A dense precondition does not hold; the legacy operator serves."""


def _utc(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive clock")
    return parsed.astimezone(UTC)


@contextmanager
def _read_connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
        return
    from src.state.db import get_forecasts_connection_with_world_read_only

    with get_forecasts_connection_with_world_read_only() as owned:
        yield owned


def _table(conn: sqlite3.Connection, name: str) -> str | None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    for schema in (("world", "main") if name == "observation_prints" else ("main",)):
        if schema in attached and conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone() is not None:
            return f"{schema}.{name}"
    return None


def _forecast_path(conn, *, city: str, target: date, tz: ZoneInfo, start_utc: datetime,
                   minutes: np.ndarray, decision: datetime) -> tuple[np.ndarray, list[str]]:
    """Hourly ecmwf_ifs path on the 5-min grid from captures received by the decision.

    Each capture starts at its run's initialisation, so the newest one rarely reaches back to
    local midnight minus PRE_MIN.  Every grid time takes the newest capture (captured_at <=
    decision) whose span contains it, interpolated linearly inside that capture: the freshest
    causal forecast everywhere, with no extrapolation."""
    table = _table(conn, "day0_hourly_vectors")
    if table is None:
        raise DenseUnavailable("VECTOR_TABLE_MISSING")
    rows = conn.execute(
        f"SELECT vector_id, timezone_name, times_json, temps_c_json FROM {table} "
        "WHERE model = ? AND city = ? AND target_date = ? AND julianday(captured_at) <= julianday(?) "
        "ORDER BY julianday(captured_at) DESC, vector_id DESC",
        (FORECAST_MODEL, city, target.isoformat(), decision.isoformat()),
    ).fetchall()
    sec = start_utc.timestamp() + minutes * 60.0
    out = np.full(sec.size, np.nan)
    used: list[str] = []
    for vector_id, timezone_name, times_json, temps_json in rows:
        vtz = ZoneInfo(str(timezone_name))
        xs, ys = [], []
        for raw, temp in zip(json.loads(times_json), json.loads(temps_json)):
            if temp is None or not math.isfinite(float(temp)):
                continue
            moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            moment = moment.replace(tzinfo=vtz) if moment.tzinfo is None else moment
            xs.append(moment.astimezone(UTC).timestamp())
            ys.append(float(temp))
        if len(xs) < 2:
            continue
        xs_a, ys_a = np.asarray(xs), np.asarray(ys)
        order = np.argsort(xs_a)
        xs_a, ys_a = xs_a[order], ys_a[order]
        if np.any(np.diff(xs_a) > 3600.0 + 1e-6):
            continue  # a gap in the hourly series: not a coherent path
        fill = np.isnan(out) & (sec >= xs_a[0]) & (sec <= xs_a[-1])
        if fill.any():
            out[fill] = np.interp(sec[fill], xs_a, ys_a)
            used.append(str(vector_id))
        if not np.isnan(out).any():
            break
    if np.isnan(out).any():
        raise DenseUnavailable("VECTOR_WINDOW_INCOMPLETE")
    return out, used


def _metar_rows(conn, table, *, city, station, channels, lo, hi, decision):
    """(observation instant, integer, source, receipt) of METAR content, first receipt per channel+instant."""
    from src.data.day0_fast_obs import metar_observation_time_from_raw
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    placeholders = ",".join("?" for _ in channels)
    rows = conn.execute(
        f"SELECT source_channel, publish_ts_utc, value_native, unit, station_id, raw_report, fetched_at_utc "
        f"FROM {table} WHERE city = ? AND source_channel IN ({placeholders}) "
        "AND julianday(publish_ts_utc) >= julianday(?) AND julianday(publish_ts_utc) < julianday(?) "
        f"AND {RECEIPT_US_SQL} <= ? ORDER BY {RECEIPT_US_SQL}, rowid",
        (city, *channels, (lo - timedelta(hours=2)).isoformat(), (hi + timedelta(hours=2)).isoformat(),
         receipt_us(decision)),
    ).fetchall()
    out: dict[tuple[str, datetime], tuple[int, datetime]] = {}
    for channel, published, value, unit, row_station, raw, fetched in rows:
        sid = str(row_station or "").strip().upper()
        if (sid != station and not sid.startswith(f"{station}:")) or str(unit or "").upper() != "C":
            continue
        try:
            pub, rec, val = _utc(published), _utc(fetched), float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val) or rec > decision:
            continue
        if channel == "aviationweather_metar":
            obs = metar_observation_time_from_raw(str(raw or ""), published_at=pub)
            if obs is None:
                continue
        else:
            obs = pub
        obs = obs.replace(second=0, microsecond=0)
        if lo <= obs < hi and obs <= decision:
            out.setdefault((str(channel), obs), (int(round(val)) if channel == "aviationweather_metar" else val, rec))
    return out


def _route(city_obj: Any, channel: str):
    from src.data.physical_current_sources import physical_current_sources_for_city

    for route in physical_current_sources_for_city(city_obj):
        if route.source_channel == channel:
            return route
    return None


def gather_day(conn, *, params, city_obj, metric: str, target: date, decision: datetime,
               semantics: SettlementSemantics) -> tuple[ds.DenseDay, dict[str, object]]:
    """Evidence received by ``decision`` (the information cutoff) as one DenseDay, plus its digest."""
    from src.data.station_temperature_adapters import valid_station_print
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    tz = ZoneInfo(params.timezone)
    start = datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(target + timedelta(days=1), datetime_time.min, tzinfo=tz).astimezone(UTC)
    if not start <= decision < end:
        raise DenseUnavailable("DECISION_OUTSIDE_LOCAL_DAY")
    day_minutes = (end - start).total_seconds() / 60.0
    minutes = ds.grid_minutes(day_minutes)
    forecast, vector_ids = _forecast_path(conn, city=params.city, target=target, tz=tz, start_utc=start,
                                          minutes=minutes, decision=decision)
    hour = np.asarray([(start + timedelta(minutes=float(m))).astimezone(tz).hour for m in minutes], int)
    table = _table(conn, "observation_prints")
    if table is None:
        raise DenseUnavailable("PRINT_TABLE_MISSING")
    station = params.station
    page_channel = f"noaa_wrh_{station.lower()}"
    mirror_channels = ("aviationweather_metar", f"ogimet_metar_{station.lower()}", *params.provisional_route_channels)
    rows = _metar_rows(conn, table, city=params.city, station=station,
                       channels=(page_channel, *mirror_channels), lo=start - timedelta(minutes=ds.PRE_MIN),
                       hi=end, decision=decision)
    to_min = lambda moment: (moment - start).total_seconds() / 60.0  # noqa: E731
    routine = set(params.routine_minutes)
    page, provisional, pre = [], [], []
    routes = {c: _route(city_obj, c) for c in params.provisional_route_channels}
    for (channel, obs), (value, _rec) in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        k = int(semantics.round_single(float(value)))
        t = to_min(obs)
        if channel in routes:
            # A fast route is METAR content only at its proven METAR instants (G2).
            if routes[channel] is None or not routes[channel].settlement_instant(obs):
                continue
        if t < 0:
            pre.append((t, k))
        elif channel == page_channel:
            page.append((t, k))
        else:
            provisional.append((t, k, params.page_retention))
    dense: list[tuple[float, float]] = []
    newest: datetime | None = None
    if params.dense_channel is not None:
        route = _route(city_obj, params.dense_channel)
        dense_rows = conn.execute(
            f"SELECT publish_ts_utc, value_native, unit, station_id, raw_report, fetched_at_utc FROM {table} "
            "WHERE city = ? AND source_channel = ? AND julianday(publish_ts_utc) >= julianday(?) "
            f"AND julianday(publish_ts_utc) < julianday(?) AND {RECEIPT_US_SQL} <= ? ORDER BY {RECEIPT_US_SQL}, rowid",
            (params.city, params.dense_channel, (start - timedelta(minutes=ds.PRE_MIN)).isoformat(),
             end.isoformat(), receipt_us(decision)),
        ).fetchall()
        seen: dict[datetime, float] = {}
        for published, value, unit, row_station, raw, fetched in dense_rows:
            try:
                obs, rec, val = _utc(published), _utc(fetched), float(value)
            except (TypeError, ValueError):
                continue
            sid = str(row_station or "").strip().upper()
            if (str(unit or "").upper() != "C" or not math.isfinite(val) or rec > decision or obs > decision
                    or (sid != station and not sid.startswith(f"{station}:"))
                    or route is None or not valid_station_print(route, str(raw or ""), observed_at=obs, value=val)):
                continue
            seen.setdefault(obs, val)
        for obs, val in seen.items():
            t = to_min(obs)
            if abs(t / ds.GRID_MIN - round(t / ds.GRID_MIN)) < 1e-9:
                dense.append((t, val))
        day_rows = [obs for obs in seen if obs >= start]
        if not day_rows:
            raise DenseUnavailable("DENSE_NO_ROWS_TODAY")
        newest = max(day_rows)
        if (decision - newest).total_seconds() / 60.0 > params.dense_max_age_minutes:
            raise DenseUnavailable("DENSE_STALE")
    schedule = [t for t in np.arange(0.0, day_minutes, 1.0)
                if int(((start + timedelta(minutes=float(t))).astimezone(UTC).minute)) in routine]
    day = ds.build_day(metric=metric, day_minutes=day_minutes, forecast=forecast, hour=hour,
                       page=page, provisional=provisional, dense=dense, schedule=schedule,
                       speci_from=to_min(decision), context=pre)
    digest = {
        "vector_ids": vector_ids,
        "forecast_sha256": hashlib.sha256(np.round(forecast, 6).tobytes()).hexdigest(),
        "page": [list(r) for r in day.page],
        "provisional": [list(r) for r in day.provisional],
        "context": [list(r) for r in day.context],
        "dense_sha256": hashlib.sha256(json.dumps(day.dense).encode()).hexdigest(),
        "dense_count": len(day.dense),
        "dense_newest_utc": None if newest is None else newest.isoformat(),
        "pending": list(day.pending),
        "speci_from_min": round(day.speci_from, 6),
        "day_minutes": day_minutes,
        "information_cutoff_utc": decision.isoformat(),
    }
    return day, digest


def state_receipt(conn, *, city: str, station: str, state: Mapping[str, object], decision: datetime) -> datetime:
    """First receipt (fetched_at_utc) of the current-state print: same channel, observation instant and value."""
    from src.data.day0_fast_obs import metar_observation_time_from_raw
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    table = _table(conn, "observation_prints")
    if table is None:
        raise DenseUnavailable("PRINT_TABLE_MISSING")
    channel = str(state["source"])
    observed = _utc(state["observed_at_utc"])
    value = float(state["value_native"])
    rows = conn.execute(
        f"SELECT publish_ts_utc, value_native, station_id, raw_report, fetched_at_utc FROM {table} "
        "WHERE city = ? AND source_channel = ? AND julianday(publish_ts_utc) >= julianday(?) "
        f"AND julianday(publish_ts_utc) <= julianday(?) AND {RECEIPT_US_SQL} <= ?",
        (city, channel, (observed - timedelta(hours=1)).isoformat(), (observed + timedelta(hours=3)).isoformat(),
         receipt_us(decision)),
    ).fetchall()
    first: datetime | None = None
    for published, row_value, row_station, raw, fetched in rows:
        sid = str(row_station or "").strip().upper()
        try:
            pub, rec, val = _utc(published), _utc(fetched), float(row_value)
        except (TypeError, ValueError):
            continue
        obs = metar_observation_time_from_raw(str(raw or ""), published_at=pub) if channel == "aviationweather_metar" else pub
        if (obs is None or obs != observed or not math.isclose(val, value, rel_tol=0.0, abs_tol=1e-9)
                or (sid != station and not sid.startswith(f"{station}:"))):
            continue
        first = rec if first is None or rec < first else first
    if first is None:
        raise DenseUnavailable("STATE_RECEIPT_UNRESOLVED")
    return first


def _bins_ok(bounds: Sequence[tuple[float | None, float | None]]) -> bool:
    ordered = sorted(bounds, key=lambda b: -math.inf if b[0] is None else b[0])
    if not ordered or ordered[0][0] is not None or ordered[-1][1] is not None:
        return False
    return all(p[1] is not None and c[0] is not None and c[0] == p[1] + 1.0 for p, c in zip(ordered, ordered[1:]))


def dense_remaining_carrier(*, metric: str, bin_bounds: Sequence[tuple[float | None, float | None]],
                            identity_inputs: Mapping[str, object], settlement_semantics: SettlementSemantics,
                            n_samples: int, resolver_terminal: Any = None,
                            conn: sqlite3.Connection | None = None) -> dict[str, object] | None:
    """The dense carrier for this family, or None when any precondition fails (legacy serves)."""
    from src.calibration.day0_dense_state_space_params import dense_params_for
    from src.config import runtime_cities_by_name

    city = str(identity_inputs.get("city") or "").strip()
    unit = str(identity_inputs.get("unit") or "").strip().upper()
    decision_text = identity_inputs.get("probability_cutoff_utc") or identity_inputs.get("decision_time_utc")
    state = identity_inputs.get("current_path_state")
    if (resolver_terminal is not None or unit != "C" or settlement_semantics.measurement_unit != "C"
            or metric not in {"high", "low"} or not decision_text or not isinstance(state, Mapping)
            or n_samples < N_SAMPLES_MIN):
        return None
    city_obj = runtime_cities_by_name().get(city)
    if city_obj is None:
        return None
    try:
        decision = _utc(decision_text)
        tz = ZoneInfo(str(city_obj.timezone))
        target = _utc(state["observed_at_utc"]).astimezone(tz).date()
    except (KeyError, TypeError, ValueError):
        return None
    qualified = dense_params_for(city, metric, target.isoformat())
    if qualified is None:
        return None
    artifact, params = qualified
    bounds = tuple((None if lo is None else float(round(lo)), None if hi is None else float(round(hi)))
                   for lo, hi in bin_bounds)
    if not _bins_ok(bounds):
        return None
    try:
        with _read_connection(conn) as reader:
            tau = state_receipt(reader, city=city, station=params.station, state=state, decision=decision)
            day, digest = gather_day(reader, params=params, city_obj=city_obj, metric=metric, target=target,
                                     decision=tau, semantics=settlement_semantics)
    except DenseUnavailable as exc:
        logger.info("DAY0_DENSE_LEGACY city=%s metric=%s reason=%s", city, metric, exc)
        return None
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        logger.warning("DAY0_DENSE_LEGACY city=%s metric=%s error=%s", city, metric, exc)
        return None
    preimage = settlement_preimage_offsets(settlement_semantics.rounding_rule,
                                           half_step=settlement_semantics.precision / 2.0)
    content = {
        "v": 7,
        "operator": DAY0_DENSE_STATE_SPACE_OPERATOR,
        "metric": metric,
        "params_artifact": artifact.content_hash,
        "city_params": params.params_hash,
        "evidence": digest,
        "bins": bounds,
        "n_samples": n_samples,
        "inputs": {k: v for k, v in identity_inputs.items() if k not in {"decision_time_utc", "probability_cutoff_utc"}},
        "settlement_semantics": {
            "resolution_source": settlement_semantics.resolution_source,
            "measurement_unit": settlement_semantics.measurement_unit,
            "precision": settlement_semantics.precision,
            "rounding_rule": settlement_semantics.rounding_rule,
        },
    }
    identity = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _CACHE.get(identity)
        if cached is not None:
            _CACHE.move_to_end(identity)
            return dict(cached)
    try:
        point = ds.bin_probabilities(params.model, day, bounds, preimage=preimage)
        variants = [ds.bin_probabilities(v, day, bounds, preimage=preimage) for v in params.variants] or [point]
    except ValueError as exc:
        logger.warning("DAY0_DENSE_LEGACY city=%s metric=%s compute_error=%s", city, metric, exc)
        return None
    rng = np.random.default_rng(int(identity[:16], 16))
    pick = rng.integers(0, len(variants), n_samples)
    samples = np.asarray(variants)[pick]
    support = ds.semantic_support(day, bounds)
    digest["semantic_support"] = list(support)
    digest["semantic_boundary"] = day.boundary_absorbing
    carrier = {
        "q": [float(x) for x in point],
        "samples": [[float(x) for x in row] for row in samples],
        "content_identity": identity,
        "operator": DAY0_DENSE_STATE_SPACE_OPERATOR,
        "sample_count": n_samples,
        "dense_evidence": {"params_artifact": artifact.content_hash, "city_params": params.params_hash, **digest},
    }
    with _CACHE_LOCK:
        _CACHE[identity] = carrier
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return dict(carrier)
