# Created: 2026-10-07
# Last reused or audited: 2026-10-08
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (D2 lifecycle, D3 information set, D4 prepared request, R1 builder seam).
"""Admitted evidence, sealing and the typed SELECT/REPLAY seam of the Day0 dense carrier.

Information set (D3).  A SELECT evaluation is a new decision at the cut
``identity_inputs['probability_cutoff_utc']``: every row received by the cut is admitted
(``fetched_at_utc <= cut``, exact receipt microseconds), and the latest received version of each
instant wins.  The admitted evidence is sealed into the carrier (``dense_evidence['sealed']``).
A REPLAY evaluation rebuilds q from that sealed evidence plus the content-addressed city parameters
alone.  There is no DB read, so replay at any later clock reproduces the certificate byte for byte.

One prepared request (D4).  ``prepare_dense_request`` performs the full qualification and the
construction:
  - a fitted, eligible city/metric after its training cutoff;
  - station-valid dense rows today, fresh at the cut;
  - complete forecast coverage and a valid day.
It returns the sealed evidence or a typed reason.  Seed fast-tail suppression, observation-lag
detection and operator dispatch all consult this one object, so they cannot disagree.

Report lifecycle (D2).  A received provisional report (AWC, Ogimet or a native report route) that
the page has not resolved becomes a mark.  Its branch weights are the product of:
  - the station's measured lifecycle prior (kept / corrected / removed / gross, per report kind);
  - the visibility likelihood of every page fetch whose returned span covers the report instant
    without the row: (1 - a(lag)) on the kept and corrected branches.
The shared outage latent and the corrected-value distribution come from the same fitted lifecycle.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import hashlib
import json
import logging
import math
import sqlite3
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from src.contracts.settlement_semantics import SettlementSemantics, settlement_preimage_offsets
from src.data import day0_dense_state_space as ds

UTC = timezone.utc
DAY0_DENSE_STATE_SPACE_OPERATOR = "dense_observation_state_space_extreme_v1"
SEALED_SCHEMA = "day0_dense_sealed_evidence_v1"
FORECAST_MODEL = "ecmwf_ifs"
PAGE_FETCH_COVER_MINUTES = 180.0  # every WRH batch fetch requests recent=180
logger = logging.getLogger(__name__)


class DenseUnavailable(Exception):
    """A dense precondition does not hold; the legacy operator serves (SELECT only)."""


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


def _local_day(target: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(target + timedelta(days=1), datetime_time.min, tzinfo=tz).astimezone(UTC)
    return start, end


def _station_ok(sid: object, station: str) -> bool:
    s = str(sid or "").strip().upper()
    return s == station or s.startswith(f"{station}:")


# ---------------------------------------------------------------- admitted rows

def _forecast_path(conn, *, city: str, target: date, start_utc: datetime, minutes: np.ndarray,
                   cut: datetime) -> tuple[np.ndarray, list[str]]:
    """Hourly ecmwf_ifs path on the 5-min grid: every grid time takes the newest capture
    (captured_at <= cut) whose span contains it, interpolated linearly inside that capture."""
    table = _table(conn, "day0_hourly_vectors")
    if table is None:
        raise DenseUnavailable("VECTOR_TABLE_MISSING")
    rows = conn.execute(
        f"SELECT vector_id, timezone_name, times_json, temps_c_json FROM {table} "
        "WHERE model = ? AND city = ? AND target_date = ? AND julianday(captured_at) <= julianday(?) "
        "ORDER BY julianday(captured_at) DESC, vector_id DESC",
        (FORECAST_MODEL, city, target.isoformat(), cut.isoformat()),
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


def _route(city_obj: Any, channel: str):
    from src.data.physical_current_sources import physical_current_sources_for_city

    for route in physical_current_sources_for_city(city_obj):
        if route.source_channel == channel:
            return route
    return None


def _native_channels(city_obj) -> tuple[str, ...]:
    from src.data.physical_current_sources import RouteKind, physical_current_sources_for_city

    return tuple(r.source_channel for r in physical_current_sources_for_city(city_obj)
                 if r.kind is RouteKind.NATIVE_REPORT)


@dataclass(frozen=True)
class Report:
    """A received METAR-content report at its own instant."""

    k: int
    received: datetime
    speci: bool


def _page_versions(conn, table, *, city, station, lo, hi, cut, semantics):
    """Page instant -> integer of its latest received version; plus the intraday fetch receipts.

    Page rows are station- and unit-validated and read in receipt order, so a later received
    correction (16 -> 15) replaces the earlier version.  Only the intraday adapter's rows (JSON
    envelope, ``recent=180`` request) date a fetch whose window is [receipt - 180 min, receipt];
    the next-day daily product covers a past day and is no intraday absence evidence."""
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    rows = conn.execute(
        f"SELECT publish_ts_utc, value_native, unit, station_id, fetched_at_utc, raw_report FROM {table} "
        "WHERE city = ? AND source_channel = ? AND julianday(publish_ts_utc) >= julianday(?) "
        f"AND julianday(publish_ts_utc) < julianday(?) AND {RECEIPT_US_SQL} <= ? ORDER BY {RECEIPT_US_SQL}, rowid",
        (city, f"noaa_wrh_{station.lower()}", lo.isoformat(), hi.isoformat(), receipt_us(cut)),
    ).fetchall()
    latest: dict[datetime, int] = {}
    receipts: set[datetime] = set()
    for published, value, unit, sid, fetched, raw in rows:
        try:
            t, rec, v = _utc(published).replace(second=0, microsecond=0), _utc(fetched), float(value)
        except (TypeError, ValueError):
            continue
        if not _station_ok(sid, station) or str(unit or "").upper() != "C" or not math.isfinite(v) or rec > cut:
            continue
        latest[t] = int(semantics.round_single(v))
        if str(raw or "").lstrip().startswith("{"):
            receipts.add(rec)
    return latest, sorted(receipts)


def _reports(conn, table, *, city, station, channels, lo, hi, cut, routine) -> dict[datetime, Report]:
    """Received METAR-content reports at their own instants: the first receipt of each instant
    across channels, the integer of its latest received version.  A report off the station's
    routine minutes, or headed SPECI, is a SPECI."""
    from src.data.day0_fast_obs import metar_observation_time_from_raw
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    placeholders = ",".join("?" for _ in channels)
    rows = conn.execute(
        f"SELECT source_channel, publish_ts_utc, value_native, unit, station_id, raw_report, fetched_at_utc "
        f"FROM {table} WHERE city = ? AND source_channel IN ({placeholders}) "
        "AND julianday(publish_ts_utc) >= julianday(?) AND julianday(publish_ts_utc) < julianday(?) "
        f"AND {RECEIPT_US_SQL} <= ? ORDER BY {RECEIPT_US_SQL}, rowid",
        (city, *channels, (lo - timedelta(hours=2)).isoformat(), (hi + timedelta(hours=2)).isoformat(), receipt_us(cut)),
    ).fetchall()
    out: dict[datetime, Report] = {}
    for channel, published, value, unit, sid, raw, fetched in rows:
        try:
            pub, rec, val = _utc(published), _utc(fetched), float(value)
        except (TypeError, ValueError):
            continue
        if not _station_ok(sid, station) or str(unit or "").upper() != "C" or not math.isfinite(val) or rec > cut:
            continue
        text = str(raw or "")
        if channel == "aviationweather_metar":
            obs = metar_observation_time_from_raw(text, published_at=pub)
            if obs is None:
                continue
        else:
            obs = pub
        obs = obs.replace(second=0, microsecond=0)
        if not lo <= obs < hi or obs > cut or not float(val).is_integer():
            continue
        speci = "SPECI" in text.upper() or obs.minute not in routine
        prior = out.get(obs)
        out[obs] = Report(int(val), rec if prior is None else min(prior.received, rec),
                           speci or (prior is not None and prior.speci))
    return out


def _dense_rows(conn, table, *, city_obj, params, lo, hi, cut) -> list[tuple[datetime, float]]:
    from src.data.station_temperature_adapters import valid_station_print
    from src.state.schema.observation_prints_schema import RECEIPT_US_SQL, receipt_us

    route = _route(city_obj, params.dense_channel)
    if route is None:
        raise DenseUnavailable("DENSE_ROUTE_MISSING")
    rows = conn.execute(
        f"SELECT publish_ts_utc, value_native, unit, station_id, raw_report, fetched_at_utc FROM {table} "
        "WHERE city = ? AND source_channel = ? AND julianday(publish_ts_utc) >= julianday(?) "
        f"AND julianday(publish_ts_utc) < julianday(?) AND {RECEIPT_US_SQL} <= ? ORDER BY {RECEIPT_US_SQL}, rowid",
        (params.city, params.dense_channel, lo.isoformat(), hi.isoformat(), receipt_us(cut)),
    ).fetchall()
    latest: dict[datetime, float] = {}
    for published, value, unit, sid, raw, fetched in rows:
        try:
            obs, rec, val = _utc(published), _utc(fetched), float(value)
        except (TypeError, ValueError):
            continue
        if (str(unit or "").upper() != "C" or not math.isfinite(val) or rec > cut or obs > cut
                or not _station_ok(sid, params.station)
                or not valid_station_print(route, str(raw or ""), observed_at=obs, value=val)):
            continue
        latest[obs] = val
    return sorted(latest.items())


def _schedule(params, start: datetime, end: datetime) -> list[datetime]:
    """Routine report instants of the local day (UTC minutes in ``routine_minutes``)."""
    out, t = [], start.replace(second=0, microsecond=0)
    minutes = set(params.routine_minutes)
    while t < end:
        if t.minute in minutes:
            out.append(t)
        t += timedelta(minutes=1)
    return out


def _missed(lc, fetches: Sequence[datetime], t: datetime) -> float:
    """Visibility likelihood of absence: the product of (1 - a(lag)) over every page fetch whose
    returned span [rec - 180 min, rec] covers t."""
    out = 1.0
    for rec in fetches:
        if rec - timedelta(minutes=PAGE_FETCH_COVER_MINUTES) <= t <= rec:
            out *= 1.0 - lc.a((rec - t).total_seconds() / 60.0)
    return out


# ---------------------------------------------------------------- the prepared request (D4)

@dataclass(frozen=True)
class OfflineInputs:
    """Walk-forward qualification inputs: candidate city parameters and a forecast-path source
    ``forecast(start_utc, grid_minutes) -> array``."""

    params: Any
    forecast: Any


@dataclass(frozen=True)
class PreparedDense:
    """The sealed dense request for one family at one cut, or the typed reason it is unavailable."""

    sealed: Mapping[str, object] | None
    reason: str | None

    @property
    def serves(self) -> bool:
        return self.sealed is not None


def prepare_dense_request(conn, *, city: str, metric: str, target: date, cut: datetime,
                          semantics: SettlementSemantics, offline: "OfflineInputs | None" = None) -> PreparedDense:
    """Full qualification and construction of the dense evidence for one family at ``cut``.

    ``offline`` is for the walk-forward qualification only: it supplies the candidate parameters
    (in place of the eligible artifact block) and the forecast path, and keeps every other rule."""
    from src.calibration.day0_dense_state_space_params import dense_params_for
    from src.config import runtime_cities_by_name

    if semantics.measurement_unit != "C" or metric not in {"high", "low"}:
        return PreparedDense(None, "UNIT_OR_METRIC_UNSUPPORTED")
    if offline is None:
        qualified = dense_params_for(city, metric, target.isoformat())
        if qualified is None:
            return PreparedDense(None, "NOT_QUALIFIED")
        artifact, params = qualified
        artifact_hash, qualification_hash = artifact.content_hash, artifact.qualification_hash
    else:
        params, artifact_hash, qualification_hash = offline.params, "offline", None
    city_obj = runtime_cities_by_name().get(city)
    if city_obj is None or str(getattr(city_obj, "wu_station", "") or "").upper() != params.station:
        return PreparedDense(None, "STATION_MISMATCH")
    if params.dense_channel is None:
        return PreparedDense(None, "NO_DENSE_CHANNEL")
    tz = ZoneInfo(params.timezone)
    start, end = _local_day(target, tz)
    if not start <= cut < end:
        return PreparedDense(None, "CUT_OUTSIDE_LOCAL_DAY")
    try:
        table = _table(conn, "observation_prints")
        if table is None:
            raise DenseUnavailable("PRINT_TABLE_MISSING")
        day_minutes = (end - start).total_seconds() / 60.0
        minutes = ds.grid_minutes(day_minutes)
        if offline is None:
            forecast, vector_ids = _forecast_path(conn, city=city, target=target, start_utc=start, minutes=minutes,
                                                  cut=cut)
        else:
            forecast, vector_ids = offline.forecast(start, minutes), ["offline"]
        hour = [int((start + timedelta(minutes=float(m))).astimezone(tz).hour) for m in minutes]
        lo = start - timedelta(minutes=ds.PRE_MIN)
        dense = _dense_rows(conn, table, city_obj=city_obj, params=params, lo=lo, hi=end, cut=cut)
        newest = dense[-1][0] if dense else None
        if newest is None or newest < start:
            raise DenseUnavailable("DENSE_ABSENT_TODAY")
        if (cut - newest).total_seconds() / 60.0 > params.dense_max_age_minutes:
            raise DenseUnavailable("DENSE_STALE")
        page, fetches = _page_versions(conn, table, city=city, station=params.station, lo=start, hi=end, cut=cut,
                                       semantics=semantics)
        channels = ("aviationweather_metar", f"ogimet_metar_{params.station.lower()}", *_native_channels(city_obj))
        reports = _reports(conn, table, city=city, station=params.station, channels=channels, lo=lo, hi=end, cut=cut,
                           routine=set(params.routine_minutes))
    except DenseUnavailable as exc:
        return PreparedDense(None, str(exc))
    except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        logger.warning("DAY0_DENSE_PREPARE_ERROR city=%s metric=%s error=%s", city, metric, exc)
        return PreparedDense(None, f"PREPARE_ERROR:{type(exc).__name__}")
    sealed = assemble_sealed(
        params=params, artifact_hash=artifact_hash, qualification_hash=qualification_hash,
        city=city, metric=metric, target=target, start=start, end=end, cut=cut, forecast=forecast, hour=hour,
        vector_ids=vector_ids, page=page, fetches=fetches, reports=reports, dense=dense)
    return PreparedDense(sealed, None)


def assemble_sealed(*, params, artifact_hash: str, qualification_hash, city: str, metric: str, target: date,
                    start: datetime, end: datetime, cut: datetime, forecast, hour, vector_ids,
                    page: Mapping[datetime, int], fetches: Sequence[datetime],
                    reports: Mapping[datetime, "Report"], dense: Sequence[tuple[datetime, float]]) -> dict:
    """The sealed evidence of one family at one cut from admitted rows (pure; production and the
    offline qualification share it)."""
    lc = params.lifecycle
    to_min = lambda moment: round((moment - start).total_seconds() / 60.0, 6)  # noqa: E731
    marks = []
    for t, rep in sorted(reports.items()):
        if t < start or t in page:
            continue
        wk, wc, wr, wg = lc.branches("speci" if rep.speci else "routine")
        miss = _missed(lc, fetches, t)
        marks.append([to_min(t), rep.k, wk * miss, wc * miss, wr, wg])
    reported = set(page) | {t for t in reports if t >= start}
    rk, rc, _rr, _rg = lc.branches("routine")
    pending = []
    for t in _schedule(params, start, end):
        if t in reported:
            continue
        wk, wc = rk, rc
        if t <= cut:
            # A routine instant already past with no report received: it joins the tape only if
            # it still appears.  Its kept/corrected weight carries the absence likelihoods of the
            # covering page fetches and of the report feeds at the cut, renormalised.
            miss = _missed(lc, fetches, t) * (1.0 - lc.a((cut - t).total_seconds() / 60.0))
            joint = rk * miss + rc * miss + (1.0 - rk - rc)
            wk, wc = rk * miss / joint, rc * miss / joint
        pending.append([to_min(t), wk, wc])
    return {
        "schema": SEALED_SCHEMA,
        "city": city, "metric": metric, "target_date": target.isoformat(),
        "probability_cutoff_utc": cut.isoformat(),
        "params_artifact": artifact_hash, "city_params": params.params_hash,
        "qualification_hash": qualification_hash,
        "day_minutes": (end - start).total_seconds() / 60.0,
        "forecast": [round(float(v), 6) for v in forecast], "hour": [int(h) for h in hour], "vector_ids": list(vector_ids),
        "page": [[to_min(t), int(k)] for t, k in sorted(page.items())],
        "marks": marks, "pending": pending,
        "context": [[to_min(t), rep.k] for t, rep in sorted(reports.items()) if t < start],
        "dense": [[to_min(t), round(float(x), 3)] for t, x in dense],
        "dense_count": len(dense), "dense_newest_utc": dense[-1][0].isoformat() if dense else None,
        "delta": [list(d) for d in lc.delta],
        "speci_from": to_min(cut), "speci_rate": params.speci_rate_per_min, "outage_prior": lc.outage_prior,
    }


def dense_serves(conn, *, city: str, metric: str, target_date: str, decision: datetime) -> bool:
    """Whether the one prepared dense request at ``decision`` serves this family (D4)."""
    from src.config import runtime_cities_by_name

    city_obj = runtime_cities_by_name().get(city)
    if city_obj is None:
        return False
    try:
        return prepare_dense_request(conn, city=city, metric=metric, target=date.fromisoformat(str(target_date)[:10]),
                                     cut=decision.astimezone(UTC),
                                     semantics=SettlementSemantics.for_city(city_obj)).serves
    except (sqlite3.Error, ValueError):
        return False


# ---------------------------------------------------------------- sealed evidence -> q

def _day_from_sealed(sealed: Mapping[str, object]) -> ds.DenseDay:
    return ds.DenseDay(
        metric=str(sealed["metric"]), day_minutes=float(sealed["day_minutes"]),
        forecast=tuple(float(v) for v in sealed["forecast"]), hour=tuple(int(h) for h in sealed["hour"]),
        page=tuple((float(t), int(k)) for t, k in sealed["page"]),
        marks=tuple((float(t), int(k), float(a), float(b), float(c), float(d)) for t, k, a, b, c, d in sealed["marks"]),
        pending=tuple((float(t), float(a), float(b)) for t, a, b in sealed["pending"]),
        context=tuple((float(t), int(k)) for t, k in sealed["context"]),
        dense=tuple((float(t), float(x)) for t, x in sealed["dense"]),
        delta=tuple((int(d), float(p)) for d, p in sealed["delta"]),
        speci_from=float(sealed["speci_from"]), speci_rate=float(sealed["speci_rate"]),
        outage_prior=float(sealed["outage_prior"]),
    )


def _bins_ok(bounds: Sequence[tuple[float | None, float | None]]) -> bool:
    ordered = sorted(bounds, key=lambda b: -math.inf if b[0] is None else b[0])
    if not ordered or ordered[0][0] is not None or ordered[-1][1] is not None:
        return False
    return all(p[1] is not None and c[0] is not None and c[0] == p[1] + 1.0 for p, c in zip(ordered, ordered[1:]))


def carrier_from_sealed(*, sealed: Mapping[str, object], params, metric: str, bounds, identity_inputs,
                        semantics: SettlementSemantics, n_samples: int) -> dict[str, object]:
    """q, parameter-variant samples and identity from sealed evidence; pure."""
    preimage = settlement_preimage_offsets(semantics.rounding_rule, half_step=semantics.precision / 2.0)
    content = {
        "v": 8, "operator": DAY0_DENSE_STATE_SPACE_OPERATOR, "metric": metric, "sealed": sealed,
        "bins": [list(b) for b in bounds], "n_samples": n_samples,
        "inputs": {k: v for k, v in identity_inputs.items() if k not in {"decision_time_utc", "probability_cutoff_utc"}},
        "settlement_semantics": {
            "resolution_source": semantics.resolution_source, "measurement_unit": semantics.measurement_unit,
            "precision": semantics.precision, "rounding_rule": semantics.rounding_rule,
        },
    }
    identity = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    day = _day_from_sealed(sealed)
    point = ds.bin_probabilities(params.model, day, bounds, preimage=preimage)
    variants = [ds.bin_probabilities(v, day, bounds, preimage=preimage) for v in params.variants] or [point]
    rng = np.random.default_rng(int(identity[:16], 16))
    samples = np.asarray(variants)[rng.integers(0, len(variants), n_samples)]
    return {
        "q": [float(x) for x in point],
        "samples": [[float(x) for x in row] for row in samples],
        "content_identity": identity,
        "operator": DAY0_DENSE_STATE_SPACE_OPERATOR,
        "sample_count": n_samples,
        "dense_evidence": {"sealed": json.loads(json.dumps(sealed)),
                           "semantic_support": list(ds.semantic_support(day, bounds)),
                           "semantic_boundary": day.boundary_absorbing},
    }


def dense_carrier_for_evaluation(*, evaluation, operator, sealed_dense, metric: str, bin_bounds,
                                 identity_inputs: Mapping[str, object], settlement_semantics: SettlementSemantics,
                                 n_samples: int, conn: sqlite3.Connection | None = None) -> dict[str, object] | None:
    """The R1 seam.  SELECT with no operator: the dense carrier, or None (legacy serves).  REPLAY with
    the dense operator: the carrier from ``sealed_dense`` alone, or a typed failure.  Otherwise None."""
    from src.calibration.day0_dense_state_space_params import load_dense_params, params_for_hash
    from src.config import runtime_cities_by_name
    from src.data.day0_hourly_vectors import Day0CarrierEvaluation

    bounds = tuple((None if lo is None else float(round(lo)), None if hi is None else float(round(hi)))
                   for lo, hi in bin_bounds)
    if evaluation is Day0CarrierEvaluation.REPLAY:
        if operator != DAY0_DENSE_STATE_SPACE_OPERATOR:
            if sealed_dense is not None:
                raise ValueError("DAY0_DENSE_SEALED_EVIDENCE_OPERATOR_MISMATCH")
            return None
        if not isinstance(sealed_dense, Mapping) or sealed_dense.get("schema") != SEALED_SCHEMA:
            raise ValueError("DAY0_DENSE_REPLAY_SEALED_EVIDENCE_MISSING")
        params = params_for_hash(str(sealed_dense.get("city")), str(sealed_dense.get("city_params")))
        if params is None:
            raise ValueError("DAY0_DENSE_REPLAY_PARAMS_MISSING")
        if str(sealed_dense.get("metric")) != metric or not _bins_ok(bounds):
            raise ValueError("DAY0_DENSE_REPLAY_SEALED_EVIDENCE_MISMATCH")
        try:
            return carrier_from_sealed(sealed=sealed_dense, params=params, metric=metric, bounds=bounds,
                                       identity_inputs=identity_inputs, semantics=settlement_semantics,
                                       n_samples=n_samples)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DAY0_DENSE_REPLAY_SEALED_EVIDENCE_INVALID") from exc
    if sealed_dense is not None:
        raise ValueError("DAY0_DENSE_SEALED_EVIDENCE_ON_SELECT")
    if operator is not None:
        return None
    city = str(identity_inputs.get("city") or "").strip()
    cut_text = identity_inputs.get("probability_cutoff_utc")
    state = identity_inputs.get("current_path_state")
    if (str(identity_inputs.get("unit") or "").upper() != "C" or not cut_text or not isinstance(state, Mapping)
            or not _bins_ok(bounds) or n_samples < 1):
        return None
    city_obj = runtime_cities_by_name().get(city)
    if city_obj is None:
        return None
    try:
        cut = _utc(cut_text)
        target = _utc(state["observed_at_utc"]).astimezone(ZoneInfo(str(city_obj.timezone))).date()
    except (KeyError, TypeError, ValueError):
        return None
    with _read_connection(conn) as reader:
        prepared = prepare_dense_request(reader, city=city, metric=metric, target=target, cut=cut,
                                         semantics=settlement_semantics)
    if not prepared.serves:
        logger.info("DAY0_DENSE_LEGACY city=%s metric=%s reason=%s", city, metric, prepared.reason)
        return None
    artifact = load_dense_params()
    params = None if artifact is None else artifact.cities.get(city)
    if params is None or params.params_hash != prepared.sealed["city_params"]:
        return None
    try:
        return carrier_from_sealed(sealed=prepared.sealed, params=params, metric=metric, bounds=bounds,
                                   identity_inputs=identity_inputs, semantics=settlement_semantics,
                                   n_samples=n_samples)
    except ValueError as exc:
        logger.warning("DAY0_DENSE_LEGACY city=%s metric=%s compute_error=%s", city, metric, exc)
        return None
