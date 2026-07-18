# Created: 2026-06-10
# Last reused or audited: 2026-07-16
# Authority basis: day0 first-principles review 2026-06-10 §6.2 (live obs hook)
#   + operator green-light 2026-06-10 (free METAR fast lane; no paid sources);
#   NOAA/NWS station files provide current-exposure priority transport, cycle
#   files provide the global delta, and aviationweather.gov provides recovery.
"""Day0 fast observation lane: free METAR feed for the running-extreme tracker.

First principles
----------------
The day0 absorbing boundary is driven by the settlement station's running
extreme. WU (the Polymarket settlement reference) publishes the same ASOS/METAR
stream after the observation. NOAA/NWS station files expose exact held-family
updates, cycle files expose global METAR deltas, and aviationweather.gov
supplies bounded history and recovery. This module emits DAY0_EXTREME_UPDATED
events when the running extreme moves.

Provenance law (source + authority on every datum):
- source_id "aviationweather_metar"; station identity validated against the
  city's configured settlement station (city.wu_station). The METAR station IS
  the physical settlement sensor; only the distribution channel differs from WU.
- observation_available_at = the feed's receiptTime (the honest publication
  clock), NOT our fetch wall-clock. Events therefore carry true latency.
- WU stays settlement truth: this lane NEVER writes settlement values; it only
  advances the day0 running-extreme boundary, and the parallel WU lane is used
  by src/data/day0_oracle_anomaly.py to cross-check for oracle anomalies
  (Paris CDG sensor-tampering class, April 2026).

Unit law (F-settled cities)
---------------------------
METAR temperatures are Celsius. US ASOS METARs carry the T-group (tenths of a
degree C) — converting tenths-C to F is exact to <0.1F. A report WITHOUT a
T-group is whole-degree C; converting it to F can be off by ~1F at bin
boundaries, which could falsely KILL an alive bin. Fail-closed rule: at
F-settled cities, reports without a T-group are SKIPPED for extreme tracking
(understating the running extreme is monotone-safe; overstating is not).
C-settled cities consume whole-C reports exactly.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

UTC = timezone.utc

AVIATIONWEATHER_METAR_ENDPOINT = "https://aviationweather.gov/api/data/metar"
NOAA_METAR_CYCLE_ENDPOINT = (
    "https://tgftp.nws.noaa.gov/data/observations/metar/cycles/{hour}Z.TXT"
)
NOAA_METAR_STATION_ENDPOINT = (
    "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{station}.TXT"
)

#: Canonical source id carried in event payload provenance.
FAST_OBS_SOURCE_ID = "aviationweather_metar"

#: T-group (temperature to tenths C) presence in the raw METAR remarks,
#: e.g. "T02110150". Required for F-settled extreme tracking (see module doc).
_T_GROUP_RE = re.compile(r"\bT\d{8}\b")
_CYCLE_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
_METAR_TEMP_RE = re.compile(r"(?:^|\s)(M?\d{2})/(?:M?\d{2}|//)(?:\s|$)")

#: Standalone-emitter throttle. The scheduled ingest lane sets this to zero
#: because its own cadence drives the cycle cursor; AWC recovery stays bounded.
DEFAULT_MIN_FETCH_INTERVAL_S = 90.0
METAR_AWC_RECOVERY_INTERVAL_S = 90.0
#: Maximum cache age (seconds) at which the fast lane may serve the ENTRY gate
#: (monitor fallback — Option B). Kills are staleness-safe; entries are not.
#: At 15 min the cache is still fresh enough that the running extreme it
#: encodes is a valid local-day extreme for entry-probability computation.
FAST_LANE_ENTRY_MAX_CACHE_AGE_S = 900.0  # 15 minutes

_MemoKey = tuple[str, str, str]
_MemoUpdate = tuple[Optional[int], Optional[int]]

# Soft entry signal for tomorrow's LOW markets. These are defaults only; the
# live evaluator uses the deployed empirical residual model's policy. The
# window is trailing as-of, not fixed to target midnight, so the runtime anchor
# matches the historical calibration surface.
PRE_DAY0_LOW_CARRYOVER_LOOKBACK_HOURS = 1.0
PRE_DAY0_LOW_CARRYOVER_MAX_LEAD_HOURS = 12.0


def _absorbs(metric: str, value: int, previous: Optional[int]) -> bool:
    return (
        previous is None
        or (metric == "high" and value > previous)
        or (metric == "low" and value < previous)
    )


def _positive_float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is invalid; using %.1fs", name, raw, default)
        return default
    return max(minimum, value)


def _positive_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is invalid; using %d", name, raw, default)
        return default
    return max(minimum, value)


DEFAULT_METAR_FETCH_TIMEOUT_S = _positive_float_env(
    "ZEUS_DAY0_METAR_FETCH_TIMEOUT_SECONDS",
    4.0,
)
METAR_FULL_FETCH_HOURS = 36.0
METAR_INCREMENTAL_FETCH_HOURS = 0.5
METAR_BACKFILL_FETCH_HOURS = 2.0
METAR_BACKFILL_INTERVAL_S = 15.0 * 60.0
METAR_RECOVERY_OVERLAP_HOURS = 0.25
METAR_HTTP_LIMITS = httpx.Limits(
    max_keepalive_connections=1,
    max_connections=2,
    keepalive_expiry=90.0,
)

DAY0_ANOMALY_CHECK_BUDGET_S = _positive_float_env(
    "ZEUS_DAY0_ANOMALY_CHECK_BUDGET_SECONDS",
    8.0,
)
DAY0_ANOMALY_CHECK_MAX_CITIES = _positive_int_env(
    "ZEUS_DAY0_ANOMALY_CHECK_MAX_CITIES",
    6,
)


@dataclass(frozen=True)
class FastObsSource:
    """Per-city fast-lane source descriptor (the source registry entry)."""

    source_id: str
    station_id: str
    authority: str  # provenance authority class for the stream
    notes: str = ""
    #: Settlement units a reading is shifted by, toward the absorbing
    #: direction, before it may enter the day0 running belief. 0.0 for a
    #: settlement-faithful station; >0.0 for a measured-but-not-faithful
    #: station with an adequate sample (Seoul/RKSI class — see
    #: day0_oracle_anomaly.metar_margin_units_for_city). Never negative.
    margin_units: float = 0.0


def fast_obs_source_for_city(city: Any) -> Optional[FastObsSource]:
    """Resolve the fast-lane source for a city, or None when no free fast lane.

    Registry policy (operator constraint: free sources only):
      - wu_icao cities -> aviationweather.gov METAR for the SAME ICAO station
        the WU settlement page reads. Covers all 50 wu_icao cities including
        international (NOAA redistributes global METAR; measured 3-6 min).
      - hko (Hong Kong) -> None here. HKO open data is free and faster but has
        its own client/lane (settlement_source_type='hko' settles on HKO, not
        WU; cross-source semantics differ). SPEC'd, not wired in this pass.
      - noaa (Istanbul/Moscow/Tel Aviv) -> None (ogimet METAR lanes already
        exist for these; day0 families for them are not WU-settled).
    """
    source_type = str(getattr(city, "settlement_source_type", "") or "")
    station = str(getattr(city, "wu_station", "") or "").strip().upper()
    if source_type == "wu_icao" and station:
        # SETTLEMENT-FAITHFULNESS MARGIN (operator correction 2026-06-10,
        # measured config/wu_metar_divergence.json; ABSORBED not excluded as
        # of 2026-07-16 day0 defect-5): a station whose METAR integer is NOT
        # reliably WU's settlement integer (Seoul/RKSI class: +-1C on ~4.5%
        # of reports) used to be excluded from the fast lane entirely, even
        # though the margin-absorption machinery to include it safely already
        # existed one layer over (day0_hard_fact_exit._metar_kill_margin_units)
        # — binary exclusion where margin machinery already exists is the
        # same disease as the climatology-band defect. A measured-but-not-
        # faithful station with an adequate sample now gets a non-zero
        # margin_units instead of None: the running belief still absorbs its
        # readings, shifted toward the absorbing direction so a METAR-only
        # value must clear the measured divergence allowance. Only a THIN or
        # ABSENT divergence measurement (not enough evidence to trust even a
        # margin-adjusted inclusion) still excludes the city outright — the
        # monotone-safe direction when there is truly no calibration to lean
        # on. Lazy import avoids a module cycle.
        margin_units = 0.0
        try:
            from src.data.day0_oracle_anomaly import metar_margin_units_for_city

            city_name = str(getattr(city, "name", "") or "")
            unit = str(getattr(city, "settlement_unit", "C") or "C").upper()
            margin = metar_margin_units_for_city(city_name, unit)
            if margin is None:
                logger.warning(
                    "DAY0_FAST_OBS_CITY_EXCLUDED city=%s station=%s reason=metar_divergence_measurement_too_thin "
                    "(no empirical WU-vs-METAR divergence measurement to absorb; see config/wu_metar_divergence.json)",
                    city_name, station,
                )
                return None
            margin_units = margin
        except ImportError:
            pass  # faithfulness model unavailable -> registry behaves as before (margin 0)
        return FastObsSource(
            source_id=FAST_OBS_SOURCE_ID,
            station_id=station,
            authority="ICAO_STATION_NATIVE",
            notes="same physical settlement station as WU; NOAA/NWS distribution",
            margin_units=margin_units,
        )
    return None


@dataclass(frozen=True)
class MetarReport:
    station_id: str
    obs_time: datetime  # UTC, the station report valid time
    receipt_time: Optional[datetime]  # UTC, feed publication time (provenance)
    temp_c: Optional[float]
    metar_type: str
    raw: str

    @property
    def has_t_group(self) -> bool:
        return bool(_T_GROUP_RE.search(self.raw or ""))


def parse_metar_api_payload(payload: object) -> list[MetarReport]:
    """Parse the aviationweather.gov JSON payload into typed reports.

    Tolerant per-row (a malformed row is skipped with a debug log), strict on
    overall shape (non-list payload returns []).
    """
    if not isinstance(payload, list):
        return []
    out: list[MetarReport] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            station = str(row.get("icaoId") or "").strip().upper()
            obs_epoch = row.get("obsTime")
            if not station or obs_epoch is None:
                continue
            obs_time = datetime.fromtimestamp(float(obs_epoch), tz=UTC)
            receipt_raw = row.get("receiptTime")
            receipt_time = None
            if receipt_raw:
                receipt_time = datetime.fromisoformat(str(receipt_raw).replace("Z", "+00:00"))
                if receipt_time.tzinfo is None:
                    receipt_time = receipt_time.replace(tzinfo=UTC)
                receipt_time = receipt_time.astimezone(UTC)
            temp_raw = row.get("temp")
            temp_c = float(temp_raw) if temp_raw is not None else None
            out.append(
                MetarReport(
                    station_id=station,
                    obs_time=obs_time,
                    receipt_time=receipt_time,
                    temp_c=temp_c,
                    metar_type=str(row.get("metarType") or ""),
                    raw=str(row.get("rawOb") or ""),
                )
            )
        except (TypeError, ValueError, OSError, OverflowError) as exc:
            logger.debug("METAR row parse skipped: %s", exc)
    return out


def _temperature_from_raw_metar(raw: str) -> float | None:
    groups = _T_GROUP_RE.findall(raw)
    if groups:
        token = groups[-1]
        sign = -1.0 if token[1] == "1" else 1.0
        return sign * int(token[2:5]) / 10.0
    match = _METAR_TEMP_RE.search(raw)
    if match is None:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))


def parse_noaa_metar_cycle_payload(
    payload: bytes | str,
    *,
    stations: Iterable[str],
    published_at: datetime,
) -> list[MetarReport]:
    """Parse one append-only NOAA cycle-file segment for selected stations."""

    text = payload.decode("ascii", "ignore") if isinstance(payload, bytes) else payload
    selected = {
        str(station).strip().upper()
        for station in stations
        if str(station).strip()
    }
    if not selected:
        return []
    published = published_at.astimezone(UTC)
    lines = text.splitlines()
    reports: dict[tuple[str, datetime, str], MetarReport] = {}
    for index, line in enumerate(lines[:-1]):
        stamp = line.strip()
        if _CYCLE_DATE_RE.fullmatch(stamp) is None:
            continue
        raw = lines[index + 1].strip()
        if not raw:
            continue
        tokens = raw.split()
        station_index = 1 if tokens and tokens[0] in {"METAR", "SPECI"} else 0
        if len(tokens) <= station_index:
            continue
        station = tokens[station_index].strip().upper()
        if station not in selected:
            continue
        try:
            observed = datetime.strptime(stamp, "%Y/%m/%d %H:%M").replace(
                tzinfo=UTC
            )
            temp_c = _temperature_from_raw_metar(raw)
        except (TypeError, ValueError):
            continue
        report = MetarReport(
            station_id=station,
            obs_time=observed,
            receipt_time=published,
            temp_c=temp_c,
            metar_type="SPECI" if tokens[0] == "SPECI" else "METAR",
            raw=raw,
        )
        reports[(station, observed, raw)] = report
    return list(reports.values())


@dataclass
class NoaaMetarCycleCursor:
    """Read only appended bytes from NOAA's current global METAR cycle file."""

    endpoint: str = NOAA_METAR_CYCLE_ENDPOINT
    _cycle_key: str | None = field(default=None, init=False)
    _offset: int = field(default=0, init=False)

    def poll(
        self,
        *,
        client: httpx.Client,
        stations: Iterable[str],
        as_of: datetime,
        timeout: float = DEFAULT_METAR_FETCH_TIMEOUT_S,
    ) -> tuple[list[MetarReport], bool]:
        now = as_of.astimezone(UTC)
        cycle_key = now.strftime("%Y%m%d%H")
        offset = self._offset if self._cycle_key == cycle_key else 0
        headers: dict[str, str] = {}
        if offset:
            headers["Accept-Encoding"] = "identity"
            headers["Range"] = f"bytes={offset}-"
        url = self.endpoint.format(hour=now.strftime("%H"))
        try:
            response = client.get(url, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            logger.warning(
                "NOAA_METAR_CYCLE_FETCH_FAILED exc=%s: %s",
                type(exc).__name__,
                exc,
            )
            return [], False

        if response.status_code == 416:
            total_match = re.search(
                r"\*/(\d+)$", response.headers.get("content-range", "")
            )
            if total_match is not None and int(total_match.group(1)) < offset:
                self._cycle_key = None
                self._offset = 0
                return [], False
            return [], True
        if response.status_code not in {200, 206}:
            logger.warning(
                "NOAA_METAR_CYCLE_HTTP_%s hour=%s",
                response.status_code,
                now.strftime("%H"),
            )
            return [], False

        try:
            published = parsedate_to_datetime(response.headers["last-modified"])
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published = published.astimezone(UTC)
        except (KeyError, TypeError, ValueError, OverflowError):
            logger.warning(
                "NOAA_METAR_CYCLE_MISSING_PUBLICATION_CLOCK hour=%s",
                now.strftime("%H"),
            )
            return [], False

        body = response.content
        if response.status_code == 206:
            total_match = re.search(
                r"/(\d+)$", response.headers.get("content-range", "")
            )
            if total_match is None:
                return [], False
            new_offset = int(total_match.group(1))
            delta = body
        else:
            new_offset = len(body)
            delta = body[offset:] if offset and len(body) >= offset else body

        self._cycle_key = cycle_key
        self._offset = new_offset
        reports = parse_noaa_metar_cycle_payload(
            delta,
            stations=stations,
            published_at=published,
        )
        if offset == 0:
            cutoff = now - timedelta(minutes=10)
            reports = [report for report in reports if report.obs_time >= cutoff]
        return reports, True


_StationFetchResult = tuple[str, list[MetarReport], bool, str | None]


@dataclass
class NoaaMetarStationCursor:
    """Bounded conditional polling for current-exposure station files."""

    endpoint: str = NOAA_METAR_STATION_ENDPOINT
    max_workers: int = 4
    _modified: dict[str, str] = field(default_factory=dict, init=False)
    _in_flight: dict[str, Future[_StationFetchResult]] = field(
        default_factory=dict,
        init=False,
    )
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _fetch(
        self,
        *,
        client: httpx.Client,
        station: str,
        modified: str | None,
        timeout: float,
    ) -> _StationFetchResult:
        headers = {"If-Modified-Since": modified} if modified else {}
        try:
            response = client.get(
                self.endpoint.format(station=station),
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "NOAA_METAR_STATION_FETCH_FAILED station=%s exc=%s: %s",
                station,
                type(exc).__name__,
                exc,
            )
            return station, [], False, modified
        if response.status_code == 304:
            return station, [], True, modified
        if response.status_code != 200:
            logger.warning(
                "NOAA_METAR_STATION_HTTP_%s station=%s",
                response.status_code,
                station,
            )
            return station, [], False, modified
        modified = response.headers.get("last-modified")
        try:
            published = parsedate_to_datetime(str(modified))
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published = published.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "NOAA_METAR_STATION_MISSING_PUBLICATION_CLOCK station=%s",
                station,
            )
            return station, [], False, None
        return (
            station,
            parse_noaa_metar_cycle_payload(
                response.content,
                stations=(station,),
                published_at=published,
            ),
            True,
            modified,
        )

    def _consume(
        self,
        station: str,
        future: Future[_StationFetchResult],
    ) -> tuple[list[MetarReport], bool]:
        try:
            result_station, reports, source_ok, modified = future.result()
        except Exception as exc:  # noqa: BLE001 - isolate one station worker
            logger.warning(
                "NOAA_METAR_STATION_WORKER_FAILED station=%s exc=%s: %s",
                station,
                type(exc).__name__,
                exc,
            )
            return [], False
        with self._lock:
            if modified:
                self._modified[result_station] = modified
        return reports, source_ok

    def poll(
        self,
        *,
        client: httpx.Client,
        stations: Iterable[str],
        timeout: float = DEFAULT_METAR_FETCH_TIMEOUT_S,
        budget_s: float = 0.75,
    ) -> tuple[list[MetarReport], bool]:
        selected = tuple(
            dict.fromkeys(
                station
                for raw in stations
                if (station := str(raw).strip().upper())
            )
        )
        with self._lock:
            ready = {
                station: future
                for station, future in self._in_flight.items()
                if future.done()
            }
            for station in ready:
                self._in_flight.pop(station, None)
            if selected and self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=max(1, int(self.max_workers)),
                    thread_name_prefix="day0-station",
                )
            for station in selected:
                if station in ready or station in self._in_flight:
                    continue
                assert self._executor is not None
                self._in_flight[station] = self._executor.submit(
                    self._fetch,
                    client=client,
                    station=station,
                    modified=self._modified.get(station),
                    timeout=timeout,
                )
            pending = {
                future: station for station, future in self._in_flight.items()
            }

        reports: list[MetarReport] = []
        source_ok = False
        for station, future in ready.items():
            station_reports, station_ok = self._consume(station, future)
            reports.extend(station_reports)
            source_ok = source_ok or station_ok

        deadline = time.monotonic() + max(0.0, float(budget_s))
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                break
            for future in done:
                station = pending.pop(future)
                with self._lock:
                    if self._in_flight.get(station) is future:
                        self._in_flight.pop(station, None)
                station_reports, station_ok = self._consume(station, future)
                reports.extend(station_reports)
                source_ok = source_ok or station_ok
        return reports, source_ok

    def close(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


def fetch_metar_reports(
    stations: Iterable[str],
    *,
    hours: float = 36.0,
    timeout: float = DEFAULT_METAR_FETCH_TIMEOUT_S,
    endpoint: str = AVIATIONWEATHER_METAR_ENDPOINT,
    client: httpx.Client | None = None,
) -> list[MetarReport]:
    """One batched fetch for all stations. Fail-soft: any error returns []."""
    ids = ",".join(sorted({str(s).strip().upper() for s in stations if str(s).strip()}))
    if not ids:
        return []
    try:
        get = client.get if client is not None else httpx.get
        resp = get(
            endpoint,
            params={"ids": ids, "format": "json", "hours": hours},
            timeout=timeout,
            headers={"User-Agent": "zeus-day0-fast-obs/1.0"},
        )
        if resp.status_code != 200:
            logger.warning("METAR_FAST_LANE_HTTP_%s ids=%s", resp.status_code, ids[:120])
            return []
        return parse_metar_api_payload(resp.json())
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("METAR_FAST_LANE_FETCH_FAILED ids=%s exc=%s: %s", ids[:120], type(exc).__name__, exc)
        return []


def settlement_temp_for_report(report: MetarReport, unit: str) -> Optional[float]:
    """Convert a METAR temp to the city's settlement unit under the unit law.

    C city: whole/tenths C verbatim. F city: requires the T-group (tenths-C)
    so the C->F conversion is exact; whole-C reports return None (skipped,
    fail-closed — see module docstring).
    """
    if report.temp_c is None:
        return None
    u = str(unit).upper()
    if u == "C":
        return float(report.temp_c)
    if u == "F":
        if not report.has_t_group:
            return None
        return float(report.temp_c) * 9.0 / 5.0 + 32.0
    return None


@dataclass(frozen=True)
class FastObsExtremes:
    city: str
    station_id: str
    target_date: str
    unit: str
    high_so_far: Optional[float]
    low_so_far: Optional[float]
    current_temp: Optional[float]
    first_obs_time: Optional[datetime]
    last_obs_time: Optional[datetime]
    last_receipt_time: Optional[datetime]
    sample_count: int
    skipped_unit_law: int
    held_implausible: int = 0


@dataclass(frozen=True)
class PreDay0LowWindow:
    """Late T-1 observation window that may softly inform tomorrow's LOW.

    This is not a Day0 hard fact. It is a station/unit/time qualified, fresh
    observation feature for entry probability conditioning before local
    midnight. The target-day low can still occur later and lower.
    """

    city: str
    station_id: str
    target_date: str
    unit: str
    window_start_time: datetime
    target_start_time: datetime
    window_low: float
    current_temp: float
    low_obs_time: datetime
    first_obs_time: datetime
    last_obs_time: datetime
    last_receipt_time: Optional[datetime]
    sample_count: int
    skipped_unit_law: int
    held_implausible: int = 0


# --- METAR PLAUSIBILITY BOUND (adversarial review 2026-06-10 fix 4) ---------
# One corrupt/spoofed METAR value must not permanently ratchet the monotone
# running extreme (emission is irreversible by design). SPIKE RULE: a value
# whose step from the previous accepted report exceeds the physical rate
# bound is accepted ONLY when the NEXT report corroborates it (stays within
# the bound of the suspect value). The LATEST report (no next yet) with an
# implausible step is held PENDING corroboration — the next fetch cycle
# re-evaluates it with its successor present. Genuine frontal jumps
# corroborate within one report interval (~30-60 min) — bounded delay, never
# a permanent loss.
# Held prints are excluded from extremes (no bin-kill), counted on the
# extremes object, WARN-logged, and reported to the oracle-anomaly module.
#
# 2026-07-16 (day0 defect-3, operator directive): a second gate used to run
# BEFORE this one — an absolute band from the city's monthly climatology
# (config/city_monthly_bounds.json p01/p99) that held any value outright
# regardless of corroboration. Deleted: METAR is an official published
# aviation feed, the same class of source the settlement chain already
# trusts, and a climatology censor on it fires hardest on exactly the
# extreme-weather days that are the highest-value trades (2026-07-14 Paris:
# 11 consecutive, mutually consistent 32-35C reports held outright because a
# forecast-ensemble-derived band capped at 31.9C — the readings were real,
# not noise, and the gate had no way to tell the difference). The spike rule
# below is not climatology-based — it is a fixed physical rate-of-change
# bound, independent of city or month — and stays; it is what actually
# catches a corrupted transmission.
_MAX_PLAUSIBLE_STEP_PER_HOUR = {"C": 10.0, "F": 18.0}
_MIN_STEP_ALLOWANCE = {"C": 3.0, "F": 5.4}
_MIN_STEP_DT_HOURS = 1.0 / 12.0  # treat sub-5-min gaps as 5 min for the bound


def _step_exceeds(prev: tuple[datetime, float], cur: tuple[datetime, float], unit: str) -> bool:
    dt_hours = max(_MIN_STEP_DT_HOURS, abs((cur[0] - prev[0]).total_seconds()) / 3600.0)
    allowed = _MIN_STEP_ALLOWANCE.get(unit, 5.4) + _MAX_PLAUSIBLE_STEP_PER_HOUR.get(unit, 18.0) * dt_hours
    return abs(cur[1] - prev[1]) > allowed


def filter_plausible_values(
    values: list[tuple[datetime, float, Optional[datetime]]],
    *,
    unit: str,
    city_name: str,
    month: int,
) -> tuple[list[tuple[datetime, float, Optional[datetime]]], int]:
    """(accepted, held_count). ``values`` must be time-sorted."""
    accepted: list[tuple[datetime, float, Optional[datetime]]] = []
    held = 0
    for index, item in enumerate(values):
        ts, value, receipt = item
        if accepted and _step_exceeds((accepted[-1][0], accepted[-1][1]), (ts, value), unit):
            nxt = values[index + 1] if index + 1 < len(values) else None
            corroborated = nxt is not None and not _step_exceeds((ts, value), (nxt[0], nxt[1]), unit)
            if not corroborated:
                held += 1
                logger.warning(
                    "METAR_PRINT_HELD city=%s reason=%s value=%.1f%s prev=%.1f%s ts=%s",
                    city_name,
                    "implausible_step_pending_corroboration" if nxt is None else "isolated_spike",
                    value, unit, accepted[-1][1], unit, ts.isoformat(),
                )
                continue
        accepted.append(item)
    return accepted, held


def running_extremes_for_local_day(
    reports: Iterable[MetarReport],
    *,
    city: Any,
    target_date: date | str,
    as_of: Optional[datetime] = None,
    margin_units: float = 0.0,
) -> FastObsExtremes:
    """Running extremes over the city-local target day from METAR reports.

    Local-day membership via ZoneInfo on the report obs time (DST-correct).
    ``as_of`` truncates samples at/before that UTC instant — used by the
    oracle-anomaly detector to compare against a slower WU snapshot over the
    SAME observation window. Implausible prints are held (fix 4) before
    extremes are computed — for emission AND for the anomaly comparison.

    ``margin_units`` (2026-07-16 day0 defect-5): shifts high_so_far/low_so_far
    toward the absorbing direction (HIGH: -margin; LOW: +margin) before
    returning — see day0_oracle_anomaly.metar_margin_units_for_city. 0.0 for
    a settlement-faithful station (no-op, current_temp is never shifted, it
    is diagnostic only). Callers that compare against a DIFFERENT source at
    face value (the WU-vs-METAR anomaly detector) must NOT pass a margin —
    shifting by the already-known divergence would blunt its own detection
    of a NEW divergence beyond what's already characterized.
    """
    tz = ZoneInfo(str(getattr(city, "timezone")))
    unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
    station = str(getattr(city, "wu_station", "") or "").strip().upper()
    target = date.fromisoformat(str(target_date)[:10]) if not isinstance(target_date, date) else target_date

    values: list[tuple[datetime, float, Optional[datetime]]] = []
    skipped = 0
    for report in reports:
        if report.station_id != station:
            continue
        if as_of is not None and report.obs_time > as_of:
            continue
        if report.obs_time.astimezone(tz).date() != target:
            continue
        value = settlement_temp_for_report(report, unit)
        if value is None:
            if report.temp_c is not None:
                skipped += 1
            continue
        values.append((report.obs_time, value, report.receipt_time))

    values.sort(key=lambda item: item[0])
    city_name = str(getattr(city, "name", ""))
    values, held = filter_plausible_values(
        values, unit=unit, city_name=city_name, month=target.month
    )
    if held:
        try:
            from src.data.day0_oracle_anomaly import note_metar_held

            note_metar_held(
                city_name, target.isoformat(),
                detail=f"{held} implausible METAR print(s) held (station {station})",
            )
        except Exception:  # noqa: BLE001 — notification is best-effort
            pass
    if not values:
        return FastObsExtremes(
            city=city_name, station_id=station,
            target_date=target.isoformat(), unit=unit,
            high_so_far=None, low_so_far=None, current_temp=None,
            first_obs_time=None, last_obs_time=None, last_receipt_time=None,
            sample_count=0, skipped_unit_law=skipped,
            held_implausible=held,
        )
    temps = [v for _, v, _ in values]
    receipts = [r for _, _, r in values if r is not None]
    return FastObsExtremes(
        city=city_name, station_id=station,
        target_date=target.isoformat(), unit=unit,
        high_so_far=max(temps) - margin_units, low_so_far=min(temps) + margin_units,
        current_temp=temps[-1],
        first_obs_time=values[0][0], last_obs_time=values[-1][0],
        last_receipt_time=max(receipts) if receipts else None,
        sample_count=len(values), skipped_unit_law=skipped,
        held_implausible=held,
    )


def pre_day0_low_window_for_target(
    reports: Iterable[MetarReport],
    *,
    city: Any,
    target_date: date | str,
    as_of: Optional[datetime] = None,
    lookback_hours: float = PRE_DAY0_LOW_CARRYOVER_LOOKBACK_HOURS,
    max_lead_hours: float = PRE_DAY0_LOW_CARRYOVER_MAX_LEAD_HOURS,
) -> Optional[PreDay0LowWindow]:
    """Return the late-evening T-1 LOW window for a future target local day.

    The window is bounded to ``[as_of - lookback, as_of]`` and only active
    while ``as_of`` is strictly before the target local day begins. This
    deliberately excludes the full prior-day low: a cold print at 06:00 on T-1
    is not evidence that tomorrow's 00:00-02:00 low has already been locked in.
    """
    try:
        tz = ZoneInfo(str(getattr(city, "timezone")))
        unit = str(getattr(city, "settlement_unit", "F") or "F").upper()
        station = str(getattr(city, "wu_station", "") or "").strip().upper()
        target = date.fromisoformat(str(target_date)[:10]) if not isinstance(target_date, date) else target_date
        ref = (as_of or datetime.now(UTC))
        if ref.tzinfo is None:
            return None
        ref = ref.astimezone(UTC)
        target_start_local = datetime.combine(target, datetime.min.time(), tzinfo=tz)
        target_start_utc = target_start_local.astimezone(UTC)
        lead_hours = (target_start_utc - ref).total_seconds() / 3600.0
        if lead_hours <= 0.0 or lead_hours > float(max_lead_hours):
            return None
        lookback = max(0.25, float(lookback_hours))
        window_start_utc = ref - timedelta(hours=lookback)
        previous_local_day = target - timedelta(days=1)
    except Exception:
        return None

    values: list[tuple[datetime, float, Optional[datetime]]] = []
    skipped = 0
    for report in reports:
        if report.station_id != station:
            continue
        obs_time = report.obs_time.astimezone(UTC)
        if obs_time < window_start_utc or obs_time > ref:
            continue
        if obs_time.astimezone(tz).date() != previous_local_day:
            continue
        value = settlement_temp_for_report(report, unit)
        if value is None:
            if report.temp_c is not None:
                skipped += 1
            continue
        values.append((obs_time, value, report.receipt_time))

    values.sort(key=lambda item: item[0])
    city_name = str(getattr(city, "name", ""))
    values, held = filter_plausible_values(
        values, unit=unit, city_name=city_name, month=previous_local_day.month
    )
    if not values:
        return None
    temps = [v for _, v, _ in values]
    low_idx = int(min(range(len(values)), key=lambda i: values[i][1]))
    receipts = [r for _, _, r in values if r is not None]
    return PreDay0LowWindow(
        city=city_name,
        station_id=station,
        target_date=target.isoformat(),
        unit=unit,
        window_start_time=window_start_utc,
        target_start_time=target_start_utc,
        window_low=float(temps[low_idx]),
        current_temp=float(temps[-1]),
        low_obs_time=values[low_idx][0],
        first_obs_time=values[0][0],
        last_obs_time=values[-1][0],
        last_receipt_time=max(receipts) if receipts else None,
        sample_count=len(values),
        skipped_unit_law=skipped,
        held_implausible=held,
    )


def fast_obs_to_day0_observation(
    *,
    city: Any,
    extremes: FastObsExtremes,
    metric: str,
    source: FastObsSource,
) -> dict[str, Any]:
    """Build the Day0 observation dict (hard-fact-gate schema) from METAR extremes.

    Every status field is computed here, fail-closed: any failed check yields a
    non-MATCH status and the reactor's 8-field hard-fact gate
    (src/events/reactor.py _day0_hard_fact_payload_live_eligible) rejects the
    event for live. The same physical settlement station + DST-unambiguous
    local-date match + unit law are the authorization basis.
    """
    from src.events.triggers.day0_extreme_updated import _observation_local_date_status

    if metric not in {"high", "low"}:
        raise ValueError(f"unsupported Day0 metric: {metric}")
    raw_value = extremes.high_so_far if metric == "high" else extremes.low_so_far
    if raw_value is None or extremes.last_obs_time is None:
        raise ValueError("fast-obs extremes carry no value for metric")

    observation_time = extremes.last_obs_time.astimezone(UTC).isoformat()
    # PUBLICATION CLOCK (PR#404 operator review P2): observation_available_at is
    # the SOURCE's publication time (feed receiptTime), never our fetch wall
    # clock — mixing "when we parsed it" into "when the source published it" is
    # a causality/evidence contamination. When the feed omits receiptTime the
    # payload falls back to the observation valid time (a conservative lower
    # bound that can never claim later-than-true availability) AND live
    # authority is DENIED below (publication_clock MISSING -> the reactor
    # hard-fact gate rejects live use; the value may still serve the monotone
    # kill memo).
    publication_clock_present = extremes.last_receipt_time is not None
    available_at = (
        extremes.last_receipt_time.astimezone(UTC).isoformat()
        if publication_clock_present
        else observation_time
    )
    expected_station = str(getattr(city, "wu_station", "") or "").strip().upper()
    station_match = "MATCH" if expected_station and extremes.station_id == expected_station else "MISMATCH"
    source_match = (
        "MATCH"
        if str(getattr(city, "settlement_source_type", "") or "") == "wu_icao" and station_match == "MATCH"
        else "MISMATCH"
    )
    local_date_status, dst_status = _observation_local_date_status(
        observation_time=observation_time,
        city_timezone=str(getattr(city, "timezone", "") or ""),
        target_date=extremes.target_date,
    )
    unit = str(getattr(city, "settlement_unit", "") or "").upper()
    rounding_status = "MATCH" if unit and extremes.unit == unit else "MISMATCH"
    source_authorized = (
        "AUTHORIZED"
        if (
            source_match == "MATCH"
            and station_match == "MATCH"
            and rounding_status == "MATCH"
            and extremes.sample_count > 0
        )
        else "UNAUTHORIZED"
    )
    live_authority = (
        "live"
        if (
            source_authorized == "AUTHORIZED"
            and local_date_status == "MATCH"
            and dst_status == "UNAMBIGUOUS"
            and publication_clock_present
        )
        else "blocked"
    )
    return {
        "city": str(getattr(city, "name", "") or ""),
        "target_date": extremes.target_date,
        "metric": metric,
        "settlement_source": source.source_id,
        "station_id": extremes.station_id,
        "observation_time": observation_time,
        "observation_available_at": available_at,
        "raw_value": float(raw_value),
        "high_so_far": extremes.high_so_far,
        "low_so_far": extremes.low_so_far,
        "source_match_status": source_match,
        "local_date_status": local_date_status,
        "station_match_status": station_match,
        "dst_status": dst_status,
        "metric_match_status": "MATCH",
        "rounding_status": rounding_status,
        "source_authorized_status": source_authorized,
        "live_authority_status": live_authority,
        "settlement_unit": unit,
        "settlement_precision": 1.0,
        "rounding_rule": "wmo_half_up",
        "observation_context_id": (
            f"metar_fast:{extremes.station_id}:{extremes.target_date}:{available_at}"
        ),
        # 2026-07-16 (day0 defect-5): extremes.high_so_far/low_so_far already
        # have source.margin_units absorbed (see running_extremes_for_local_day)
        # for a measured-but-not-settlement-faithful station — record the
        # applied margin so raw_value vs the pre-margin METAR reading stays
        # reconstructable (pre-margin = raw_value + margin for HIGH,
        # raw_value - margin for LOW) without re-deriving it from a divergence
        # config that could be regenerated later with a different number.
        "metar_margin_units_applied": float(source.margin_units),
    }


#: Source freshness states for one fetch pass (PR#404 operator review P0-3).
FETCH_FRESH = "fresh_fetch"                      # live fetch succeeded this pass
FETCH_CACHE_HIT = "cache_hit"                    # cache younger than the fetch interval
FETCH_STALE_AFTER_FAILURE = "stale_cache_after_failure"  # fetch failed; serving old cache
FETCH_NO_DATA = "no_data"                        # fetch failed; no cache exists


@dataclass(frozen=True)
class FastObsPrefetch:
    """Pure in-memory result of the HTTP phase (PR#404 operator review P0-2).

    Produced OUTSIDE any DB write mutex by :meth:`Day0FastObsEmitter.prefetch`;
    consumed INSIDE the mutex by :meth:`Day0FastObsEmitter.emit_prefetched`
    (which performs only EventWriter writes — no network).
    """

    eligible: tuple  # tuple[(city, FastObsSource, local_target_date_iso), ...]
    reports: tuple   # tuple[MetarReport, ...]
    freshness_status: str
    cache_age_s: Optional[float]
    decision_time: datetime
    anomaly_actions: tuple = ()
    # Reports whose publication identities have not yet been confirmed through
    # this emitter's ledger write. ``None`` preserves compatibility for callers
    # that construct FastObsPrefetch directly; those callers request the legacy
    # full-report append behavior. Production prefetches always set a tuple.
    ledger_reports: tuple | None = None


def _report_publication_key(report: MetarReport) -> tuple[str, str, float] | None:
    if report.temp_c is None:
        return None
    publish_ts = report.receipt_time or report.obs_time
    return (
        str(report.station_id).strip().upper(),
        publish_ts.astimezone(UTC).isoformat(),
        float(report.temp_c),
    )


def _report_observation_key(
    report: MetarReport,
) -> tuple[str, str, float | None]:
    return (
        str(report.station_id).strip().upper(),
        report.obs_time.astimezone(UTC).isoformat(),
        None if report.temp_c is None else float(report.temp_c),
    )


def _merge_report_windows(
    cached: list[MetarReport],
    fetched: list[MetarReport],
) -> list[MetarReport]:
    """Merge reports, retaining the earliest publication of each observation."""
    by_observation: dict[tuple[str, str, float | None], MetarReport] = {}
    for report in (*cached, *fetched):
        key = _report_observation_key(report)
        previous = by_observation.get(key)
        report_published = report.receipt_time or report.obs_time
        if previous is None:
            by_observation[key] = report
            continue
        previous_published = previous.receipt_time or previous.obs_time
        if report_published < previous_published:
            by_observation[key] = report
    reports = list(by_observation.values())
    if not reports:
        return []
    cutoff = max(report.obs_time for report in reports) - timedelta(
        hours=METAR_FULL_FETCH_HOURS
    )
    reports = [report for report in reports if report.obs_time >= cutoff]
    reports.sort(
        key=lambda report: (
            report.obs_time,
            report.station_id,
            report.receipt_time or report.obs_time,
            report.raw,
        )
    )
    return reports


def _append_metar_prints_to_ledger(
    world_conn: Any, eligible: tuple, reports: list[MetarReport]
) -> bool:
    """Append the supplied METAR publication delta for fast-eligible stations.

    Returns True when the whole delta reached SQLite (including duplicate-only
    INSERT OR IGNORE passes), False when the write failed and must be retried.

    The caller keeps the complete report window for running-extreme reduction,
    but passes only publication identities not yet confirmed through this
    emitter. This prevents a source-clock poll from re-playing the same 36-hour
    payload into SQLite every few seconds.

    Append the reports to the
    observation_prints publication-stream ledger (day0 defect-ledger,
    2026-07-16).

    One short write, already inside the caller's mutex-held world_conn — no
    network here (reports were fetched earlier, outside the mutex, in
    prefetch()). INSERT OR IGNORE dedup means a report seen on a previous
    cycle is a free no-op, never a mutation. Fail-soft: any error is logged
    and swallowed — the ledger is additive observability, not load-bearing
    for the existing emission pipeline; a failure here must never block a
    DAY0_EXTREME_UPDATED emission.

    Stores the RAW METAR temperature (always Celsius on the wire) with
    unit='C' UNCONDITIONALLY — including reports without a T-group, which
    ``settlement_temp_for_report`` skips for F-settled cities (imprecise
    whole-C->F conversion could falsely cross a bin edge). The ledger's job
    is to record what was published, not to pre-apply a city-specific
    trust decision at write time — a print stored here is exactly what
    hydrate_from_ledger later reconstructs a MetarReport from, so storing
    the SAME raw Celsius a live fetch would have produced avoids a lossy
    C->F->C round trip. The F-city T-group unit law is instead applied at
    READ time (_latest_authorized_day0_fact's ledger fact, using the stored
    raw_report text) — one rule, one place to keep in sync.
    """
    if not eligible or not reports:
        return True
    try:
        from src.state.schema.observation_prints_schema import append_print

        by_station: dict[str, list[MetarReport]] = {}
        for report in reports:
            by_station.setdefault(str(report.station_id).strip().upper(), []).append(report)

        appended = 0
        fetched_at = datetime.now(UTC).isoformat()
        seen_city_stations: set[tuple[str, str]] = set()
        for city, source, _target_date in eligible:
            station = str(source.station_id).strip().upper()
            city_name = str(getattr(city, "name", "") or "")
            key = (city_name, station)
            if key in seen_city_stations:
                continue  # one prefetch batch can list a city more than once (e.g. multi-day)
            seen_city_stations.add(key)
            for report in by_station.get(station, ()):
                if report.temp_c is None:
                    continue
                publish_ts = (
                    report.receipt_time.astimezone(UTC)
                    if report.receipt_time is not None
                    else report.obs_time.astimezone(UTC)
                )
                if append_print(
                    world_conn,
                    city=city_name,
                    station_id=report.station_id,
                    source_channel=FAST_OBS_SOURCE_ID,
                    publish_ts_utc=publish_ts.isoformat(),
                    value_native=float(report.temp_c),
                    unit="C",
                    fetched_at_utc=fetched_at,
                    raw_report=report.raw,
                ):
                    appended += 1
        if appended:
            logger.debug("OBSERVATION_PRINTS_APPENDED source=%s count=%d", FAST_OBS_SOURCE_ID, appended)
        return True
    except Exception as exc:  # noqa: BLE001 — ledger append is best-effort, never blocks emission
        logger.warning(
            "OBSERVATION_PRINTS_APPEND_FAILED source=%s exc=%s: %s",
            FAST_OBS_SOURCE_ID, type(exc).__name__, exc,
        )
        return False


@dataclass
class Day0FastObsEmitter:
    """Stateful fast-lane emitter: prefetch (HTTP) -> emit (DB writes).

    Emission policy is MONOTONE: a (city, date, metric) emits only when the
    rounded running extreme moves in the absorbing direction (high: up,
    low: down) or on first sight. Re-emissions of the same report dedup at the
    event store via the idempotency key (available_at = feed receiptTime).
    In-process memo only — a daemon restart re-emits once and dedups.

    SOURCE-FAILURE DISCIPLINE (PR#404 operator review P0-3):
      - every fetch ATTEMPT (success or failure) arms the throttle — an API
        outage can never produce a tight retry storm;
      - a failed fetch serves the old cache with an explicit
        ``stale_cache_after_failure`` status (never silently as fresh);
      - stale-after-failure data older than the city's measured staleness
        budget is NEVER emitted as a live-authority event — it may only
        advance the monotone hard-fact kill memo (kill direction is
        staleness-safe; entries are not).
    """

    fetcher: Callable[..., list[MetarReport]] = fetch_metar_reports
    min_fetch_interval_s: float = DEFAULT_MIN_FETCH_INTERVAL_S
    priority_station_poll_budget_s: float = 0.75
    _last_attempt_monotonic: float = field(default=0.0, init=False)
    _last_awc_attempt_monotonic: float = field(default=0.0, init=False)
    _cache_fetched_monotonic: float = field(default=0.0, init=False)
    _last_backfill_monotonic: float = field(default=0.0, init=False)
    _cached_reports: list[MetarReport] = field(default_factory=list, init=False)
    _full_window_loaded: bool = field(default=False, init=False)
    _cycle_cursor: NoaaMetarCycleCursor = field(
        default_factory=NoaaMetarCycleCursor,
        init=False,
    )
    _station_cursor: NoaaMetarStationCursor = field(
        default_factory=NoaaMetarStationCursor,
        init=False,
    )
    _http_client: httpx.Client | None = field(default=None, init=False, repr=False)
    # SPLIT MEMOS (PR#404 round-2 P0-1): the KILL memo (hard-fact exit source,
    # advanced by any memo-safe value incl. stale-withheld ones) and the LIVE
    # memo (emit moved-check, advanced ONLY by an INSERTED live event) were one
    # dict — a stale-after-failure withholding advanced it without emitting, so
    # a later FRESH confirmation of the same rounded extreme saw moved=False
    # and the live event NEVER emitted (entry lane silently diverged from the
    # exit lane's state). Two memos, two consumers, two update rules.
    _last_kill_memo_rounded: dict[tuple[str, str, str], int] = field(default_factory=dict, init=False)
    _last_live_emitted_rounded: dict[tuple[str, str, str], int] = field(default_factory=dict, init=False)
    _event_memo_snapshot_rowid: int | None = field(default=None, init=False)
    _event_memo_snapshot_keys: set[_MemoKey] = field(default_factory=set, init=False)
    _ledgered_report_keys: set[tuple[str, str, float]] = field(default_factory=set, init=False)
    _pending_ledger_reports: dict[tuple[str, str, float], MetarReport] = field(
        default_factory=dict,
        init=False,
    )
    _event_evaluated_report_keys: set[tuple[str, str, float]] = field(
        default_factory=set,
        init=False,
    )
    _ledger_report_keys_loaded: bool = field(default=False, init=False)
    _ledger_cursor_id: int = field(default=0, init=False)
    _anomaly_cursor: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def ledger_report_keys_loaded(self) -> bool:
        with self._lock:
            return self._ledger_report_keys_loaded

    def prefetched_events_evaluated(self, prefetch: FastObsPrefetch) -> bool:
        """True when every pending publication already crossed event commit."""

        keys = {
            key
            for report in (prefetch.ledger_reports or ())
            if (key := _report_publication_key(report)) is not None
        }
        if not keys:
            return False
        with self._lock:
            return keys <= self._event_evaluated_report_keys

    def mark_prefetched_events_evaluated(
        self,
        report_keys: Iterable[tuple[str, str, float]],
    ) -> None:
        """Record publication keys only after their event transaction commits."""

        with self._lock:
            self._event_evaluated_report_keys.update(report_keys)

    def hydrate_event_memos_from_events(
        self,
        world_conn: Any,
        eligible: tuple,
        *,
        family_admission: Callable[[dict[str, Any]], bool] | None = None,
    ) -> int:
        """Recover restart memos before the WORLD write-critical section.

        Recovery is indexed by exact city/date and reads both metrics in one
        range seek. A rowid watermark makes the result reusable while still
        detecting a concurrent DAY0 event before a later write transaction.
        """

        keys = {
            (str(getattr(city, "name", "")), str(target_date), metric)
            for city, _source, target_date in eligible
            for metric in ("high", "low")
            if str(getattr(city, "name", "")) and str(target_date)
            and (
                family_admission is None
                or family_admission(
                    {
                        "city": str(getattr(city, "name", "")),
                        "target_date": str(target_date),
                        "metric": metric,
                    }
                )
            )
        }
        if not keys:
            return 0
        try:
            row = world_conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM opportunity_events"
            ).fetchone()
            snapshot_rowid = int(row[0] or 0) if row is not None else 0
            with self._lock:
                previous_rowid = self._event_memo_snapshot_rowid
                checked = set(self._event_memo_snapshot_keys)

            day0_changed = previous_rowid is None or snapshot_rowid < previous_rowid
            if (
                not day0_changed
                and previous_rowid is not None
                and snapshot_rowid > previous_rowid
            ):
                day0_changed = world_conn.execute(
                    """
                    SELECT 1
                      FROM opportunity_events
                     WHERE rowid > ?
                       AND event_type = 'DAY0_EXTREME_UPDATED'
                     LIMIT 1
                    """,
                    (previous_rowid,),
                ).fetchone() is not None

            requested = keys if day0_changed else keys - checked
            recovered: dict[_MemoKey, int] = {}
            families = sorted({(city, target_date) for city, target_date, _metric in requested})
            for city_name, target_date in families:
                rows = world_conn.execute(
                    """
                    SELECT json_extract(payload_json, '$.metric') AS metric,
                           CASE json_extract(payload_json, '$.metric')
                               WHEN 'high' THEN MAX(CAST(json_extract(
                                   payload_json, '$.rounded_value'
                               ) AS INTEGER))
                               ELSE MIN(CAST(json_extract(
                                   payload_json, '$.rounded_value'
                               ) AS INTEGER))
                           END AS extreme
                      FROM opportunity_events
                     WHERE event_type = 'DAY0_EXTREME_UPDATED'
                       AND json_extract(payload_json, '$.city') = ?
                       AND json_extract(payload_json, '$.target_date') = ?
                       AND json_extract(payload_json, '$.metric') IN ('high', 'low')
                       AND json_extract(payload_json, '$.source_authorized_status') = 'AUTHORIZED'
                       AND json_extract(payload_json, '$.local_date_status') = 'MATCH'
                       AND json_extract(payload_json, '$.dst_status') = 'UNAMBIGUOUS'
                       AND json_extract(payload_json, '$.rounded_value') IS NOT NULL
                     GROUP BY json_extract(payload_json, '$.metric')
                    """,
                    (city_name, target_date),
                ).fetchall()
                for metric, value in rows:
                    key = (city_name, target_date, str(metric))
                    if key in requested and value is not None:
                        recovered[key] = int(value)
        except Exception as exc:  # noqa: BLE001 - restart recovery is fail-soft
            logger.debug(
                "DAY0_EVENT_MEMO_HYDRATE_FAILED exc=%s: %s",
                type(exc).__name__,
                exc,
            )
            return 0

        updates = {key: (value, value) for key, value in recovered.items()}
        self.apply_memo_updates(updates)
        with self._lock:
            if day0_changed:
                self._event_memo_snapshot_keys.clear()
            self._event_memo_snapshot_keys.update(keys)
            self._event_memo_snapshot_rowid = snapshot_rowid
        return len(recovered)

    def apply_memo_updates(self, updates: dict[_MemoKey, _MemoUpdate]) -> None:
        """Apply staged memo movement after its event transaction commits."""

        with self._lock:
            for key, (kill_value, live_value) in updates.items():
                metric = key[2]
                if kill_value is not None and _absorbs(
                    metric,
                    kill_value,
                    self._last_kill_memo_rounded.get(key),
                ):
                    self._last_kill_memo_rounded[key] = kill_value
                if live_value is not None and _absorbs(
                    metric,
                    live_value,
                    self._last_live_emitted_rounded.get(key),
                ):
                    self._last_live_emitted_rounded[key] = live_value

    def mark_event_memo_snapshot(self, world_conn: Any) -> None:
        """Advance the recovery watermark only after durable commit."""

        try:
            row = world_conn.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM opportunity_events"
            ).fetchone()
            snapshot_rowid = int(row[0] or 0) if row is not None else 0
        except Exception as exc:  # noqa: BLE001 - next read can rehydrate
            logger.debug(
                "DAY0_EVENT_MEMO_SNAPSHOT_MARK_FAILED exc=%s: %s",
                type(exc).__name__,
                exc,
            )
            return
        with self._lock:
            self._event_memo_snapshot_rowid = snapshot_rowid

    def sync_ledger_report_keys(
        self,
        world_conn: Any,
        cities: list[Any],
        *,
        as_of: datetime | None = None,
    ) -> int:
        """Seed publication identities without replacing the live report cache.

        The source-clock uses this once on process start before its first HTTP
        fetch. It prevents the fetched 36-hour history from being mistaken for
        an unpersisted delta while preserving the source's observation-time
        semantics for extreme calculation.
        """
        with self._lock:
            if self._ledger_report_keys_loaded:
                return 0
        city_names = tuple(
            dict.fromkeys(
                str(getattr(city, "name", "") or "")
                for city in cities
                if fast_obs_source_for_city(city) is not None
                and str(getattr(city, "name", "") or "")
            )
        )
        if not city_names:
            with self._lock:
                self._ledger_report_keys_loaded = True
            return 0
        placeholders = ",".join("?" for _ in city_names)
        cutoff = (
            (as_of or datetime.now(UTC)).astimezone(UTC)
            - timedelta(hours=METAR_FULL_FETCH_HOURS)
        ).isoformat()
        rows = world_conn.execute(
            f"""
            SELECT station_id, publish_ts_utc, value_native
              FROM observation_prints INDEXED BY idx_observation_prints_city_publish
             WHERE city IN ({placeholders})
               AND publish_ts_utc >= ?
               AND source_channel = ?
            """,
            (*city_names, cutoff, FAST_OBS_SOURCE_ID),
        ).fetchall()
        keys: set[tuple[str, str, float]] = set()
        for station_id, publish_ts, value_native in rows:
            try:
                keys.add(
                    (
                        str(station_id).strip().upper(),
                        str(publish_ts),
                        float(value_native),
                    )
                )
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        with self._lock:
            self._ledgered_report_keys.update(keys)
            for key in keys:
                self._pending_ledger_reports.pop(key, None)
                self._event_evaluated_report_keys.discard(key)
            self._ledger_report_keys_loaded = True
        return len(keys)

    def hydrate_from_ledger(self, world_conn: Any, eligible: tuple) -> int:
        """Restart-proofing (day0 defect-ledger, 2026-07-16): seed the
        in-process METAR cache from observation_prints instead of starting
        empty on a fresh process.

        A cold process's ``_cached_reports`` is empty until the first
        successful transport poll, and that wait is unbounded during an
        outage. Every consumer of the cache
        (latest_extremes' entry gate, emit_prefetched's own extreme
        computation) silently has NOTHING for that whole window. This is a
        BRIDGE, not a replacement for the kill-memo restart recovery
        (_recover_kill_memo_from_events, defense in depth, unchanged) —
        only the in-process cache path.

        No-op once the cache is NON-EMPTY (a successful fetch or a prior
        hydration) — that is the only state hydration must never overwrite.
        A FAILED fetch attempt (``_last_attempt_monotonic`` armed, cache
        still empty) must NOT block hydration: in the live reactor the
        prefetch always runs before emit, so its failed attempt has already
        armed that flag by the time this runs — gating on it would make
        hydration dead code in exactly the outage scenario it exists for.
        Sets
        ``_cache_fetched_monotonic`` to now: hydration IS this process's
        best current view of the world, exactly like a fresh fetch would be
        — and the normal 90s throttle means a genuine live fetch supersedes
        it almost immediately regardless.

        Fail-soft: any error is logged and swallowed; the cache simply stays
        at whatever it already was (empty, on a true cold start).
        """
        with self._lock:
            if self._cached_reports:
                return 0  # cache already warm — never overwrite live data
        if not eligible:
            return 0
        try:
            reports: list[MetarReport] = []
            seen_city_stations: set[tuple[str, str]] = set()
            for city, source, target_date in eligible:
                station = str(source.station_id).strip().upper()
                city_name = str(getattr(city, "name", "") or "")
                key = (city_name, station)
                if key in seen_city_stations:
                    continue
                seen_city_stations.add(key)
                tz = ZoneInfo(str(getattr(city, "timezone", "") or "UTC"))
                target_day = date.fromisoformat(str(target_date)[:10])
                day_start = datetime.combine(
                    target_day, datetime.min.time(), tzinfo=tz
                ).astimezone(UTC)
                day_end = day_start + timedelta(days=1)
                rows = world_conn.execute(
                    """
                    SELECT publish_ts_utc, value_native, fetched_at_utc, raw_report
                      FROM observation_prints
                     WHERE city = ? AND station_id = ? AND source_channel = ?
                       AND publish_ts_utc >= ? AND publish_ts_utc < ?
                    """,
                    (city_name, station, FAST_OBS_SOURCE_ID, day_start.isoformat(), day_end.isoformat()),
                ).fetchall()
                for row in rows:
                    try:
                        obs_time = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                        if obs_time.tzinfo is None:
                            continue
                    except (TypeError, ValueError):
                        continue
                    receipt_time = None
                    try:
                        receipt_time = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
                        if receipt_time.tzinfo is None:
                            receipt_time = None
                    except (TypeError, ValueError):
                        pass
                    reports.append(
                        MetarReport(
                            station_id=station,
                            obs_time=obs_time.astimezone(UTC),
                            receipt_time=receipt_time.astimezone(UTC) if receipt_time else None,
                            temp_c=float(row[1]),
                            metar_type="METAR",
                            raw=str(row[3] or ""),
                        )
                    )
            if not reports:
                return 0
            with self._lock:
                if self._cached_reports:
                    return 0  # a concurrent fetch beat us to it
                self._cached_reports = reports
                self._cache_fetched_monotonic = time.monotonic()
                ledgered_keys = tuple(
                    key
                    for report in reports
                    if (key := _report_publication_key(report)) is not None
                )
                self._ledgered_report_keys.update(ledgered_keys)
                for key in ledgered_keys:
                    self._pending_ledger_reports.pop(key, None)
                    self._event_evaluated_report_keys.discard(key)
            logger.info(
                "DAY0_FAST_OBS_LEDGER_HYDRATED count=%d cities=%d",
                len(reports), len(seen_city_stations),
            )
            return len(reports)
        except Exception as exc:  # noqa: BLE001 — hydration is best-effort, never blocks the caller
            logger.warning(
                "DAY0_FAST_OBS_LEDGER_HYDRATE_FAILED exc=%s: %s", type(exc).__name__, exc,
            )
            return 0

    def sync_from_ledger(
        self,
        world_conn: Any,
        cities: list[Any],
        *,
        as_of: datetime | None = None,
    ) -> int:
        """Incrementally project the canonical METAR ledger into this process.

        Cold start uses the city/time index for the retained 36-hour window.
        Later calls seek only primary keys above the last observed row. This is
        the cross-process read path for trading consumers after data-ingest
        became the sole AWC network owner.
        """

        city_names = tuple(
            dict.fromkeys(
                str(getattr(city, "name", "") or "")
                for city in cities
                if fast_obs_source_for_city(city) is not None
                and str(getattr(city, "name", "") or "")
            )
        )
        if not city_names:
            return 0
        with self._lock:
            cursor = self._ledger_cursor_id
        if cursor > 0:
            rows = world_conn.execute(
                """
                SELECT id, station_id, publish_ts_utc, value_native, raw_report
                  FROM observation_prints
                 WHERE id > ? AND source_channel = ?
                 ORDER BY id
                """,
                (cursor, FAST_OBS_SOURCE_ID),
            ).fetchall()
        else:
            placeholders = ",".join("?" for _ in city_names)
            cutoff = (
                (as_of or datetime.now(UTC)).astimezone(UTC)
                - timedelta(hours=METAR_FULL_FETCH_HOURS)
            ).isoformat()
            rows = world_conn.execute(
                f"""
                SELECT id, station_id, publish_ts_utc, value_native, raw_report
                  FROM observation_prints INDEXED BY idx_observation_prints_city_publish
                 WHERE city IN ({placeholders})
                   AND publish_ts_utc >= ?
                   AND source_channel = ?
                 ORDER BY id
                """,
                (*city_names, cutoff, FAST_OBS_SOURCE_ID),
            ).fetchall()
        if not rows:
            return 0

        reports: list[MetarReport] = []
        max_id = cursor
        for row in rows:
            try:
                row_id = int(row[0])
                max_id = max(max_id, row_id)
                published = datetime.fromisoformat(
                    str(row[2]).replace("Z", "+00:00")
                )
                if published.tzinfo is None:
                    continue
                reports.append(
                    MetarReport(
                        station_id=str(row[1]).strip().upper(),
                        obs_time=published.astimezone(UTC),
                        receipt_time=published.astimezone(UTC),
                        temp_c=float(row[3]),
                        metar_type="METAR",
                        raw=str(row[4] or ""),
                    )
                )
            except (TypeError, ValueError, OSError, OverflowError):
                continue
        with self._lock:
            self._ledger_cursor_id = max_id
            if reports:
                self._cached_reports = _merge_report_windows(
                    self._cached_reports,
                    reports,
                )
                self._cache_fetched_monotonic = time.monotonic()
                self._full_window_loaded = True
                ledgered_keys = tuple(
                    key
                    for report in reports
                    if (key := _report_publication_key(report)) is not None
                )
                self._ledgered_report_keys.update(ledgered_keys)
                for key in ledgered_keys:
                    self._pending_ledger_reports.pop(key, None)
                    self._event_evaluated_report_keys.discard(key)
        return len(reports)

    def _reports_with_status(
        self,
        stations: list[str],
        *,
        priority_stations: Iterable[str] = (),
    ) -> tuple[list[MetarReport], str, Optional[float]]:
        """Return reports and freshness with a start-to-start fetch throttle."""
        now = time.monotonic()
        with self._lock:
            cache_age = (now - self._cache_fetched_monotonic) if self._cached_reports else None
            if (now - self._last_attempt_monotonic) < self.min_fetch_interval_s:
                if self._cached_reports:
                    status = (
                        FETCH_CACHE_HIT
                        if self._cache_fetched_monotonic >= self._last_attempt_monotonic
                        else FETCH_STALE_AFTER_FAILURE
                    )
                    return list(self._cached_reports), status, cache_age
                return [], FETCH_NO_DATA, None
            self._last_attempt_monotonic = now
            fetch_hours = METAR_FULL_FETCH_HOURS
            if self._cached_reports and self._full_window_loaded:
                fetch_hours = min(
                    METAR_FULL_FETCH_HOURS,
                    max(
                        METAR_INCREMENTAL_FETCH_HOURS,
                        (cache_age or 0.0) / 3600.0 + METAR_RECOVERY_OVERLAP_HOURS,
                    ),
                )
                if (now - self._last_backfill_monotonic) >= METAR_BACKFILL_INTERVAL_S:
                    fetch_hours = max(fetch_hours, METAR_BACKFILL_FETCH_HOURS)
        reports: list[MetarReport] = []
        source_ok = False
        history_loaded = False
        if self.fetcher is fetch_metar_reports:
            with self._lock:
                if self._http_client is None:
                    self._http_client = httpx.Client(limits=METAR_HTTP_LIMITS)
                client = self._http_client
                awc_due = (
                    self._last_awc_attempt_monotonic == 0.0
                    or now - self._last_awc_attempt_monotonic
                    >= METAR_AWC_RECOVERY_INTERVAL_S
                )
                history_missing = not self._full_window_loaded
            cycle_reports: list[MetarReport] = []
            priority_reports, priority_ok = self._station_cursor.poll(
                client=client,
                stations=priority_stations,
                budget_s=self.priority_station_poll_budget_s,
            )
            reports.extend(priority_reports)
            source_ok = source_ok or priority_ok
            if priority_reports:
                cycle_reports = priority_reports
            try:
                if not priority_reports:
                    cycle_reports, cycle_ok = self._cycle_cursor.poll(
                        client=client,
                        stations=stations,
                        as_of=datetime.now(UTC),
                    )
                    reports.extend(cycle_reports)
                    source_ok = source_ok or cycle_ok
            except Exception as exc:  # noqa: BLE001 - isolate transports
                logger.warning(
                    "NOAA_METAR_CYCLE_POLL_RAISED exc=%s: %s",
                    type(exc).__name__,
                    exc,
                )
            if awc_due and (history_missing or not cycle_reports):
                with self._lock:
                    self._last_awc_attempt_monotonic = now
                try:
                    awc_reports = self.fetcher(
                        stations,
                        hours=fetch_hours,
                        client=client,
                    )
                    history_loaded = bool(awc_reports)
                    reports.extend(awc_reports)
                    source_ok = source_ok or bool(awc_reports)
                except Exception as exc:  # noqa: BLE001 - isolate transports
                    logger.warning(
                        "DAY0_FAST_OBS_RECOVERY_RAISED exc=%s: %s",
                        type(exc).__name__,
                        exc,
                    )
        else:
            try:
                reports = self.fetcher(stations, hours=fetch_hours)
                source_ok = bool(reports)
                history_loaded = bool(reports)
            except Exception as exc:  # noqa: BLE001 - injected fetcher contract
                logger.warning(
                    "DAY0_FAST_OBS_FETCH_RAISED exc=%s: %s",
                    type(exc).__name__,
                    exc,
                )
        with self._lock:
            if reports:
                previous = {
                    _report_observation_key(report): report
                    for report in self._cached_reports
                }
                base = (
                    []
                    if history_loaded and not self._full_window_loaded
                    else self._cached_reports
                )
                base_set = set(base)
                merged = (
                    list(base)
                    if base and all(report in base_set for report in reports)
                    else _merge_report_windows(base, reports)
                )
                for report in merged:
                    old = previous.get(_report_observation_key(report))
                    if old == report:
                        continue
                    key = _report_publication_key(report)
                    if key is not None and key not in self._ledgered_report_keys:
                        self._pending_ledger_reports.setdefault(key, report)
                self._cached_reports = merged
                self._full_window_loaded = self._full_window_loaded or history_loaded
                fetched_monotonic = time.monotonic()
                self._cache_fetched_monotonic = fetched_monotonic
                if history_loaded and fetch_hours >= METAR_BACKFILL_FETCH_HOURS:
                    self._last_backfill_monotonic = fetched_monotonic
                return list(self._cached_reports), FETCH_FRESH, 0.0
            cache_age = (
                (time.monotonic() - self._cache_fetched_monotonic) if self._cached_reports else None
            )
            if self._cached_reports and source_ok:
                return list(self._cached_reports), FETCH_CACHE_HIT, cache_age
            if self._cached_reports:
                logger.warning(
                    "DAY0_FAST_OBS_FETCH_FAILED serving stale cache age_s=%.0f (failure-throttled %ss)",
                    cache_age or -1.0, self.min_fetch_interval_s,
                )
                return list(self._cached_reports), FETCH_STALE_AFTER_FAILURE, cache_age
            return [], FETCH_NO_DATA, None

    def latest_rounded_extreme(
        self, city_name: str, target_date: str, metric: str, *, world_conn: Any = None
    ) -> Optional[int]:
        """Latest settlement-rounded extreme known to the fast lane for
        (city, date, metric) — the hard-fact monotone KILL source.

        Values here passed station/source/unit/local-date authorization at
        observation-build time (publication-clock or fetch-staleness may have
        been degraded — monotone kills are safe under staleness; entries are
        gated separately). Consumed by src/execution/day0_hard_fact_exit.py.
        Reads the KILL memo (round-2 P0-1 split: independent of whether a live
        event was emitted).

        RESTART-SAFE RECOVERY (2026-06-12, critique Angle 1 Gap C): the in-process
        kill memo is lost on daemon restart. Rather than persisting a NEW table,
        we recover from the DAY0_EXTREME_UPDATED events that emit_prefetched
        ALREADY persisted durably to opportunity_events (zeus-world.db). When the
        in-process memo has no value, this reads the latest memo-safe (AUTHORIZED
        + local-date MATCH + DST UNAMBIGUOUS) rounded extreme for the cell from
        those events, applies the absorbing-direction reduction (high=max,
        low=min), caches it into the in-process memo (so the live monotone emit
        logic stays consistent post-restart), and returns it. Fail-soft: any DB
        error leaves the memo untouched and returns None (the lane simply has no
        recovered fact this call).

        ``world_conn`` must be supplied by callers that hold a composite write
        connection (the production path: execute_monitoring_phase → evaluate_hard_fact_exit
        → this method). Opening an independent world connection when None was the
        old fallback; it has been deleted to prevent the connection-burst regression
        (347f713d) — see _recover_kill_memo_from_events docstring. When world_conn
        is None and the memo is cold, recovery is skipped and None is returned.
        """
        key = (str(city_name), str(target_date), str(metric))
        with self._lock:
            memo = self._last_kill_memo_rounded.get(key)
        if memo is not None:
            return memo
        # In-process memo empty (restart / first call this process): recover from
        # the durable event store before giving up.
        # GUARD: world_conn=None means no connection was threaded — skip recovery
        # (return None) rather than opening an independent connection. The production
        # call path always supplies world_conn via execute_monitoring_phase; any path
        # that does not is cold-start-safe (the memo is empty, so None is correct).
        if world_conn is None:
            return None
        recovered = _recover_kill_memo_from_events(
            city_name=str(city_name),
            target_date=str(target_date),
            metric=str(metric),
            world_conn=world_conn,
        )
        if recovered is None:
            return None
        with self._lock:
            # Re-check under lock: a concurrent emit may have populated the memo;
            # honor the absorbing direction so recovery never regresses it.
            current = self._last_kill_memo_rounded.get(key)
            if current is None or (
                (metric == "high" and recovered > current)
                or (metric == "low" and recovered < current)
            ):
                self._last_kill_memo_rounded[key] = recovered
                return recovered
            return current

    def latest_extremes(
        self,
        city: Any,
        target_date: str,
        *,
        as_of: Optional[datetime] = None,
    ) -> Optional["FastObsExtremes"]:
        """Return computed FastObsExtremes from the local METAR projection for
        ``city`` on ``target_date`` (UTC date, ISO string).

        This is the ENTRY-GATE source for Option-B monitor fallback (see
        day0_obs_fastlane_plan.md §4.2). Unlike ``latest_rounded_extreme`` (the
        monotone KILL memo), this method recomputes extremes LIVE from cached
        reports — so ``first_obs_time`` and ``sample_count`` are accurate for
        coverage-window evaluation.

        CONTRACT:
          - Returns None when the projection is empty, when the city is not
            eligible for the fast lane, or when no station-matching reports
            exist for the target date.
          - Does NOT perform any network I/O — reads only from ``_cached_reports``
            populated by the owning source clock or ``sync_from_ledger``.
          - ``as_of``: UTC instant cap passed to running_extremes_for_local_day;
            defaults to now().

        Consumed EXCLUSIVELY by observation_client._fetch_wu_observation fallback
        (Option-B wiring). Do NOT call from hot paths outside the monitor lane.
        """
        source = fast_obs_source_for_city(city)
        if source is None:
            return None
        with self._lock:
            reports = list(self._cached_reports)
            cache_monotonic = self._cache_fetched_monotonic
        if not reports:
            return None
        # Freshness gate: cache must be ≤ FAST_LANE_ENTRY_MAX_CACHE_AGE_S old.
        # Stale caches must not serve the entry gate (kills are staleness-safe;
        # entries are not — see plan §4.2 "Freshness contract").
        cache_age_s = time.monotonic() - cache_monotonic
        if cache_age_s > FAST_LANE_ENTRY_MAX_CACHE_AGE_S:
            return None
        effective_as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
        try:
            extremes = running_extremes_for_local_day(
                reports, city=city, target_date=target_date, as_of=effective_as_of,
                margin_units=source.margin_units,
            )
        except Exception as exc:
            logger.warning(
                "DAY0_FAST_OBS_LATEST_EXTREMES_FAILED city=%s exc=%s: %s",
                getattr(city, "name", "?"), type(exc).__name__, exc,
            )
            return None
        if extremes.sample_count == 0:
            return None
        return extremes

    def latest_pre_day0_low_window(
        self,
        city: Any,
        target_date: str,
        *,
        as_of: Optional[datetime] = None,
        lookback_hours: float = PRE_DAY0_LOW_CARRYOVER_LOOKBACK_HOURS,
        max_lead_hours: float = PRE_DAY0_LOW_CARRYOVER_MAX_LEAD_HOURS,
    ) -> Optional[PreDay0LowWindow]:
        """Return a fresh cached late T-1 LOW window for tomorrow's LOW entry.

        This is a probability feature, not an absorbing fact. It therefore
        shares the ENTRY freshness rule with ``latest_extremes`` and never opens
        a network request or recovers old event-store facts.
        """
        source = fast_obs_source_for_city(city)
        if source is None:
            return None
        with self._lock:
            reports = list(self._cached_reports)
            cache_monotonic = self._cache_fetched_monotonic
        if not reports:
            return None
        cache_age_s = time.monotonic() - cache_monotonic
        if cache_age_s > FAST_LANE_ENTRY_MAX_CACHE_AGE_S:
            return None
        effective_as_of = (as_of or datetime.now(UTC)).astimezone(UTC)
        try:
            return pre_day0_low_window_for_target(
                reports,
                city=city,
                target_date=target_date,
                as_of=effective_as_of,
                lookback_hours=lookback_hours,
                max_lead_hours=max_lead_hours,
            )
        except Exception as exc:
            logger.warning(
                "PRE_DAY0_LOW_WINDOW_FAILED city=%s target_date=%s exc=%s: %s",
                getattr(city, "name", "?"), target_date, type(exc).__name__, exc,
            )
            return None

    def cached_anomaly_actions(
        self,
        *,
        cities: list[Any],
        decision_time: datetime,
        anomaly_check: Callable[[Any, FastObsExtremes, list[MetarReport]], Any],
        max_cities: int = 1,
    ) -> tuple[Any, ...]:
        """Check cached METAR truth without opening another source request.

        The data-ingest source-clock owns AWC polling. This lower-cadence guard
        shares its cache, rotates across cities, and returns durable actions for
        a separate short write phase. A fetch in progress/failed or a stale
        cache cannot feed the comparison.
        """

        eligible: list[tuple[Any, FastObsSource, str]] = []
        for city in cities:
            source = fast_obs_source_for_city(city)
            if source is None:
                continue
            try:
                tz = ZoneInfo(str(city.timezone))
            except Exception:
                continue
            local_today = decision_time.astimezone(tz).date().isoformat()
            eligible.append((city, source, local_today))
        if not eligible or max_cities <= 0:
            return ()

        now = time.monotonic()
        with self._lock:
            reports = list(self._cached_reports)
            cache_age = (
                now - self._cache_fetched_monotonic
                if reports
                else None
            )
            cache_current = (
                bool(reports)
                and self._cache_fetched_monotonic >= self._last_attempt_monotonic
                and cache_age is not None
                and cache_age <= FAST_LANE_ENTRY_MAX_CACHE_AGE_S
            )
            cursor = self._anomaly_cursor % len(eligible)
        if not cache_current:
            return ()

        rotated = eligible[cursor:] + eligible[:cursor]
        actions: list[Any] = []
        visited = 0
        checked = 0
        for city, _source, target_date in rotated:
            visited += 1
            try:
                extremes = running_extremes_for_local_day(
                    reports,
                    city=city,
                    target_date=target_date,
                    as_of=decision_time.astimezone(UTC),
                )
                if not extremes.sample_count:
                    continue
                checked += 1
                action = anomaly_check(city, extremes, reports)
                if action is not None:
                    actions.append(action)
                if checked >= max_cities:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "DAY0_FAST_OBS_ANOMALY_CHECK_FAILED city=%s exc=%s: %s",
                    getattr(city, "name", "?"),
                    type(exc).__name__,
                    exc,
                )
        with self._lock:
            self._anomaly_cursor = (cursor + max(1, visited)) % len(eligible)
        return tuple(actions)

    def prefetch(
        self,
        *,
        cities: list[Any],
        decision_time: datetime,
        priority_scopes: Iterable[tuple[str, str]] = (),
        anomaly_check: Optional[Callable[[Any, FastObsExtremes, list[MetarReport]], Any]] = None,
        anomaly_check_budget_s: Optional[float] = None,
        anomaly_check_max_cities: Optional[int] = None,
    ) -> FastObsPrefetch:
        """HTTP phase: resolve eligible cities, fetch METAR (throttled), run the
        (WU-HTTP) anomaly cross-check. NO DB writes — safe to run OUTSIDE the
        world-write mutex (P0-2). Any anomaly result is returned as a durable
        action for emit_prefetched to apply with the already-open world_conn.
        Fail-soft everywhere."""
        eligible: list[tuple[Any, FastObsSource, str]] = []
        for city in cities:
            source = fast_obs_source_for_city(city)
            if source is None:
                continue
            try:
                tz = ZoneInfo(str(city.timezone))
            except Exception:
                continue
            local_today = decision_time.astimezone(tz).date().isoformat()
            eligible.append((city, source, local_today))
        if not eligible:
            return FastObsPrefetch((), (), FETCH_NO_DATA, None, decision_time)

        priority_scope_set = frozenset(
            (str(city), str(target_date)) for city, target_date in priority_scopes
        )
        reports, status, cache_age = self._reports_with_status(
            [source.station_id for _, source, _ in eligible],
            priority_stations=(
                source.station_id
                for city, source, target_date in eligible
                if (str(getattr(city, "name", "")), target_date)
                in priority_scope_set
            ),
        )
        with self._lock:
            ledger_reports = tuple(self._pending_ledger_reports.values())
        # ANOMALY-CHECK FRESHNESS GATE (PR#404 round-2 P0-2A): the WU-vs-METAR
        # cross-check must never CONCLUDE from a stale METAR cache — a METAR
        # outage plus a fresh WU update would read as divergence and falsely
        # pause the family (the pause gates entry q, hard-fact exits, AND the
        # cancel sweep). Only a fresh fetch or an in-interval cache hit may
        # feed the detector; stale/no-data passes are loudly skipped.
        anomaly_input_ok = status in (FETCH_FRESH, FETCH_CACHE_HIT)
        if reports and anomaly_check is not None and not anomaly_input_ok:
            logger.warning(
                "DAY0_ORACLE_ANOMALY_CHECK_SKIPPED_METAR_CACHE_STALE status=%s cache_age_s=%s "
                "(divergence cannot be concluded from a stale METAR window)",
                status, cache_age,
            )
        if reports and anomaly_check is not None and anomaly_input_ok:
            anomaly_actions = []
            checks_started = 0
            budget_s = (
                DAY0_ANOMALY_CHECK_BUDGET_S
                if anomaly_check_budget_s is None
                else max(0.0, anomaly_check_budget_s)
            )
            max_checks = (
                DAY0_ANOMALY_CHECK_MAX_CITIES
                if anomaly_check_max_cities is None
                else max(0, anomaly_check_max_cities)
            )
            started_monotonic = time.monotonic()
            for city, _source, target_date in eligible:
                if max_checks <= 0:
                    logger.warning(
                        "DAY0_FAST_OBS_ANOMALY_CHECK_SKIPPED_BUDGET max_checks=%d budget_s=%.3f",
                        max_checks,
                        budget_s,
                    )
                    break
                if checks_started >= max_checks:
                    logger.warning(
                        "DAY0_FAST_OBS_ANOMALY_CHECK_BUDGET_EXHAUSTED checked=%d eligible=%d "
                        "elapsed_s=%.3f budget_s=%.3f reason=max_checks",
                        checks_started,
                        len(eligible),
                        time.monotonic() - started_monotonic,
                        budget_s,
                    )
                    break
                if (
                    budget_s > 0.0
                    and checks_started > 0
                    and (time.monotonic() - started_monotonic) >= budget_s
                ):
                    logger.warning(
                        "DAY0_FAST_OBS_ANOMALY_CHECK_BUDGET_EXHAUSTED checked=%d eligible=%d "
                        "elapsed_s=%.3f budget_s=%.3f reason=elapsed",
                        checks_started,
                        len(eligible),
                        time.monotonic() - started_monotonic,
                        budget_s,
                    )
                    break
                try:
                    # No margin_units here (deliberate): this is the WU-vs-
                    # METAR divergence DETECTOR — it must compare a raw METAR
                    # extreme against WU at face value to catch a NEW/EXCESS
                    # divergence beyond what's already measured. Shifting by
                    # the already-known margin first would blunt it.
                    extremes = running_extremes_for_local_day(
                        reports, city=city, target_date=target_date,
                        as_of=decision_time.astimezone(UTC),
                    )
                    if extremes.sample_count:
                        checks_started += 1
                        action = anomaly_check(city, extremes, reports)
                        if action is not None:
                            anomaly_actions.append(action)
                except Exception as exc:  # noqa: BLE001 — detector must never block the lane
                    logger.warning(
                        "DAY0_FAST_OBS_ANOMALY_CHECK_FAILED city=%s exc=%s: %s",
                        getattr(city, "name", "?"), type(exc).__name__, exc,
                    )
            return FastObsPrefetch(
                tuple(eligible),
                tuple(reports),
                status,
                cache_age,
                decision_time,
                tuple(anomaly_actions),
                ledger_reports,
            )
        return FastObsPrefetch(
            tuple(eligible),
            tuple(reports),
            status,
            cache_age,
            decision_time,
            (),
            ledger_reports,
        )

    def emit_prefetched(
        self,
        *,
        world_conn,
        prefetch: FastObsPrefetch,
        received_at: str,
        limit: int = 50,
        day0_is_tradeable: bool = True,
        family_admission=None,
        inserted_event_ids: list[str] | None = None,
        inserted_families: list[tuple[str, str, str]] | None = None,
        evaluated_report_keys: list[tuple[str, str, float]] | None = None,
        deferred_memo_updates: dict[_MemoKey, _MemoUpdate] | None = None,
        persist_ledger: bool = True,
    ) -> int:
        """DB-write phase: emit DAY0_EXTREME_UPDATED events from a prefetch.

        Performs NO network IO (mutex-safe, P0-2). Live-authority emission is
        DENIED for stale-after-failure data older than the city's staleness
        budget and for observations without live authority (publication clock
        missing, etc.) — those may only advance the monotone kill memo (P0-3).

        ``day0_is_tradeable`` (default True) flows to the trigger so non-tradeable
        day0 events carry the lower sub-sort (2026-06-11 anti-starvation; the
        scope-aware claim tier in fetch_pending is the cross-tier authority).
        """
        from src.events.event_writer import EventWriter
        from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger
        from src.contracts.settlement_semantics import SettlementSemantics
        from src.signal.day0_obs_latency import staleness_budget_minutes
        from src.data.day0_oracle_anomaly import apply_day0_oracle_anomaly_action

        for action in getattr(prefetch, "anomaly_actions", ()) or ():
            try:
                apply_day0_oracle_anomaly_action(action, conn=world_conn)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "DAY0_ORACLE_ANOMALY_EMIT_ACTION_FAILED action=%r exc=%s: %s",
                    action, type(exc).__name__, exc,
                )
        if prefetch.eligible:
            # day0 defect-ledger (2026-07-16): cold-start restart-proofing —
            # runs even when this cycle's own fetch produced nothing
            # (prefetch.reports empty), which is exactly the scenario this
            # exists for. No-ops instantly once the cache is warm.
            self.hydrate_from_ledger(world_conn, prefetch.eligible)
        if not prefetch.eligible or not prefetch.reports:
            return 0
        reports = list(prefetch.reports)
        decision_time = prefetch.decision_time
        emission_eligible = prefetch.eligible
        if prefetch.ledger_reports is not None:
            changed_stations = {
                str(report.station_id).strip().upper()
                for report in prefetch.ledger_reports
            }
            with self._lock:
                pending_live_families = {
                    (city, target_date)
                    for (city, target_date, metric), kill_value
                    in self._last_kill_memo_rounded.items()
                    if self._last_live_emitted_rounded.get(
                        (city, target_date, metric)
                    ) != kill_value
                }
            emission_eligible = tuple(
                item
                for item in prefetch.eligible
                if (
                    str(item[1].station_id).strip().upper() in changed_stations
                    or (str(getattr(item[0], "name", "")), item[2])
                    in pending_live_families
                )
            )
            if not emission_eligible:
                return 0
        self.hydrate_event_memos_from_events(
            world_conn,
            emission_eligible,
            family_admission=family_admission,
        )
        trigger = Day0ExtremeUpdatedTrigger(
            EventWriter(world_conn),
            day0_is_tradeable=day0_is_tradeable,
            family_admission=family_admission,
        )
        pending_memo_updates: dict[_MemoKey, _MemoUpdate] = {}

        def _memo_values(key: _MemoKey) -> _MemoUpdate:
            with self._lock:
                kill_value = self._last_kill_memo_rounded.get(key)
                live_value = self._last_live_emitted_rounded.get(key)
            pending_kill, pending_live = pending_memo_updates.get(key, (None, None))
            return (
                pending_kill if pending_kill is not None else kill_value,
                pending_live if pending_live is not None else live_value,
            )

        def _stage_memo(
            key: _MemoKey,
            *,
            kill_value: int | None = None,
            live_value: int | None = None,
        ) -> None:
            pending_kill, pending_live = pending_memo_updates.get(key, (None, None))
            pending_memo_updates[key] = (
                kill_value if kill_value is not None else pending_kill,
                live_value if live_value is not None else pending_live,
            )

        emitted = 0
        attempted_stations: set[str] = set()
        failed_stations: set[str] = set()
        eligible_stations = {
            str(source.station_id).strip().upper()
            for _city, source, _target_date in emission_eligible
        }
        for city, source, target_date in emission_eligible:
            if emitted >= max(1, int(limit)):
                break
            station = str(source.station_id).strip().upper()
            attempted_stations.add(station)
            try:
                extremes = running_extremes_for_local_day(
                    reports, city=city, target_date=target_date,
                    as_of=decision_time.astimezone(UTC),
                    margin_units=source.margin_units,
                )
                if extremes.sample_count == 0:
                    continue
                city_name = str(getattr(city, "name", ""))
                stale_blocked = False
                if prefetch.freshness_status == FETCH_STALE_AFTER_FAILURE:
                    budget_s = staleness_budget_minutes(city_name) * 60.0
                    if prefetch.cache_age_s is None or prefetch.cache_age_s > budget_s:
                        stale_blocked = True
                semantics = SettlementSemantics.for_city(city)
                for metric in ("high", "low"):
                    value = extremes.high_so_far if metric == "high" else extremes.low_so_far
                    if value is None:
                        continue
                    rounded = int(semantics.round_single(float(value)))
                    key = (city_name, target_date, metric)
                    # SPLIT MEMO movement checks (round-2 P0-1): the live emit
                    # decision compares against the LIVE memo (last INSERTED
                    # event), never the kill memo — a kill-memo-only update
                    # from a withheld pass must not suppress the later live
                    # event for the same rounded extreme.
                    kill_previous, live_previous = _memo_values(key)
                    kill_moved = _absorbs(metric, rounded, kill_previous)
                    live_moved = _absorbs(metric, rounded, live_previous)
                    if not kill_moved and not live_moved:
                        continue
                    observation = fast_obs_to_day0_observation(
                        city=city, extremes=extremes, metric=metric, source=source
                    )
                    # KILL-MEMO SAFETY: only station/source/unit/local-date
                    # authorized values may advance the monotone kill memo
                    # (a wrong-day or wrong-station value must never kill bins).
                    memo_safe = (
                        observation["source_authorized_status"] == "AUTHORIZED"
                        and observation["local_date_status"] == "MATCH"
                        and observation["dst_status"] == "UNAMBIGUOUS"
                    )
                    live_ok = (
                        observation["live_authority_status"] == "live"
                        and not stale_blocked
                    )
                    kill_update = rounded if memo_safe and kill_moved else None
                    if not live_ok:
                        _stage_memo(key, kill_value=kill_update)
                        if memo_safe and kill_moved:
                            logger.warning(
                                "DAY0_FAST_OBS_LIVE_WITHHELD city=%s date=%s metric=%s "
                                "rounded=%s freshness=%s cache_age_s=%s authority=%s "
                                "(kill memo updated; no live event emitted; live memo untouched)",
                                city_name, target_date, metric, rounded,
                                prefetch.freshness_status, prefetch.cache_age_s,
                                observation["live_authority_status"],
                            )
                        continue
                    if not live_moved:
                        _stage_memo(key, kill_value=kill_update)
                        continue
                    result = trigger.emit_from_observation(
                        observation=observation,
                        settlement_semantics=semantics,
                        decision_time=decision_time,
                        received_at=received_at,
                    )
                    if result is None:
                        _stage_memo(key, kill_value=kill_update)
                        continue
                    if result.inserted or result.duplicate:
                        # A PERSISTED live event advances the live memo. `inserted`
                        # is the normal path; `duplicate` is the restart/dedup path
                        # where the immutable event already exists in world DB. If a
                        # duplicate did not advance the in-process live memo, the
                        # restarted daemon would re-attempt the same INSERT OR IGNORE
                        # every cycle until the next rounded movement. That is not a
                        # trading error, but it is not live-stable behavior either.
                        _stage_memo(
                            key,
                            kill_value=kill_update,
                            live_value=rounded,
                        )
                    if result.inserted:
                        emitted += 1
                        if inserted_event_ids is not None:
                            inserted_event_ids.append(result.event_id)
                        if inserted_families is not None:
                            inserted_families.append(
                                (city_name, target_date, metric)
                            )
                        logger.info(
                            "DAY0_FAST_OBS_EMIT city=%s date=%s metric=%s rounded=%s "
                            "obs_time=%s available_at=%s samples=%d skipped_unit_law=%d freshness=%s",
                            city_name, target_date, metric, rounded,
                            observation["observation_time"], observation["observation_available_at"],
                            extremes.sample_count, extremes.skipped_unit_law,
                            prefetch.freshness_status,
                        )
                    elif result.duplicate:
                        logger.debug(
                            "DAY0_FAST_OBS_EMIT_DUPLICATE city=%s date=%s metric=%s rounded=%s "
                            "obs_time=%s available_at=%s freshness=%s (live memo advanced)",
                            city_name, target_date, metric, rounded,
                            observation["observation_time"], observation["observation_available_at"],
                            prefetch.freshness_status,
                        )
            except Exception as exc:  # noqa: BLE001 — one city must not kill the lane
                failed_stations.add(station)
                logger.warning(
                    "DAY0_FAST_OBS_CITY_FAILED city=%s exc=%s: %s",
                    getattr(city, "name", "?"), type(exc).__name__, exc,
                )
        if evaluated_report_keys is not None:
            complete_stations = (attempted_stations - failed_stations) | {
                str(report.station_id).strip().upper()
                for report in (prefetch.ledger_reports or ())
                if str(report.station_id).strip().upper() not in eligible_stations
            }
            evaluated_report_keys.extend(
                key
                for report in (prefetch.ledger_reports or ())
                if str(report.station_id).strip().upper() in complete_stations
                and (key := _report_publication_key(report)) is not None
            )
        if persist_ledger:
            self.persist_prefetched_ledger(
                world_conn=world_conn,
                prefetch=prefetch,
            )
        if deferred_memo_updates is None:
            self.apply_memo_updates(pending_memo_updates)
        else:
            deferred_memo_updates.update(pending_memo_updates)
        return emitted

    def persist_prefetched_ledger(
        self,
        *,
        world_conn,
        prefetch: FastObsPrefetch,
    ) -> bool:
        """Persist additive publication history after the trade fact is durable.

        ``observation_prints`` makes restart recovery better, but it is not the
        executable Day0 fact. The ingest source clock calls this in a separate,
        best-effort transaction on a later non-emitting pass so ledger volume
        can never contend with a new ``DAY0_EXTREME_UPDATED`` decision.
        """

        if not prefetch.eligible:
            return True
        ledger_reports = (
            list(prefetch.reports)
            if prefetch.ledger_reports is None
            else list(prefetch.ledger_reports)
        )
        if not ledger_reports:
            return True
        if not _append_metar_prints_to_ledger(
            world_conn,
            prefetch.eligible,
            ledger_reports,
        ):
            return False
        with self._lock:
            for report in ledger_reports:
                key = _report_publication_key(report)
                if key is None:
                    continue
                self._ledgered_report_keys.add(key)
                self._pending_ledger_reports.pop(key, None)
                self._event_evaluated_report_keys.discard(key)
        return True

    def emit_events(
        self,
        *,
        world_conn,
        cities: list[Any],
        decision_time: datetime,
        received_at: str,
        limit: int = 50,
        anomaly_check: Optional[Callable[[Any, FastObsExtremes, list[MetarReport]], Any]] = None,
    ) -> int:
        """Compatibility wrapper: prefetch (HTTP) + emit (DB) in one call.

        Live wiring MUST use the split form (prefetch outside the world-write
        mutex, emit_prefetched inside) — see main._edli_event_reactor_cycle.
        """
        prefetch = self.prefetch(
            cities=cities, decision_time=decision_time, anomaly_check=anomaly_check
        )
        return self.emit_prefetched(
            world_conn=world_conn, prefetch=prefetch, received_at=received_at, limit=limit
        )


def _recover_kill_memo_from_events(
    *,
    city_name: str,
    target_date: str,
    metric: str,
    world_conn: Any,
) -> Optional[int]:
    """Recover the kill-memo rounded extreme from durably-persisted
    DAY0_EXTREME_UPDATED events (restart-safe; no new table).

    Reads opportunity_events (zeus-world.db) for the cell, keeps only memo-safe
    rows (source_authorized_status=AUTHORIZED, local_date_status=MATCH,
    dst_status=UNAMBIGUOUS — the SAME authorization the live kill memo required),
    ACROSS EVERY AUTHORIZED SOURCE for the cell (not just this emitter's own
    fast-lane source), and reduces by the absorbing direction (high=MAX,
    low=MIN). None when no recoverable row exists or on any error (fail-soft).

    2026-07-16 (day0 defect-3, operator directive): this query used to also
    filter ``settlement_source = FAST_OBS_SOURCE_ID``, so a cold in-process
    memo could only ever recover this emitter's OWN prior emissions — never
    a higher/lower extreme another source (e.g. wu_icao_history) had already
    established for the same cell. That self-blinding contradicted this very
    docstring's "restart-safe... recover the kill-memo" claim and let a
    newly-eligible fast-lane fetch treat its own first-sight value as the
    day-so-far extreme even when a truer one already existed. Deleted the
    source filter; the existing AUTHORIZED/MATCH/UNAMBIGUOUS predicates are
    already source-agnostic and are the actual authorization gate.

    ``world_conn`` MUST be supplied by the caller (a world-main read connection or
    a composite connection with zeus-world ATTACHed). Passing None raises
    RuntimeError immediately — the old "open a fresh connection when None" fallback
    has been DELETED because it caused the day0 connection-burst regression
    (commit 347f713d): 47 simultaneous per-city independent world connections opened
    inside the reactor cycle that already held the composite write lock, producing
    SQLITE_BUSY × 47 per cycle. See docs/evidence/lock_storm/
    2026-06-13_lock_storm_regression_archaeology.md for the full mechanism.
    """
    if world_conn is None:
        raise RuntimeError(
            "_recover_kill_memo_from_events: world_conn must be supplied by the caller. "
            "Opening an independent world connection here is forbidden (connection-burst "
            "antibody — see 2026-06-13 lock_storm_regression_archaeology.md)."
        )
    conn = world_conn
    try:
        agg = "MAX" if metric == "high" else "MIN"
        sql = f"""
            SELECT {agg}(CAST(json_extract(payload_json, '$.rounded_value') AS INTEGER)) AS extreme
            FROM opportunity_events
            WHERE event_type = 'DAY0_EXTREME_UPDATED'
              AND json_extract(payload_json, '$.city') = ?
              AND json_extract(payload_json, '$.target_date') = ?
              AND json_extract(payload_json, '$.metric') = ?
              AND json_extract(payload_json, '$.source_authorized_status') = 'AUTHORIZED'
              AND json_extract(payload_json, '$.local_date_status') = 'MATCH'
              AND json_extract(payload_json, '$.dst_status') = 'UNAMBIGUOUS'
              AND json_extract(payload_json, '$.rounded_value') IS NOT NULL
        """
        row = conn.execute(
            sql,
            (city_name, target_date, metric),
        ).fetchone()
        if row is None:
            return None
        value = row[0]
        return int(value) if value is not None else None
    except Exception as exc:  # noqa: BLE001 — recovery is best-effort, fail-soft
        logger.debug(
            "DAY0_KILL_MEMO_RECOVERY_FAILED city=%s date=%s metric=%s exc=%s: %s",
            city_name, target_date, metric, type(exc).__name__, exc,
        )
        return None


_EMITTER_SINGLETON: Day0FastObsEmitter | None = None
_EMITTER_LOCK = threading.Lock()


def get_fast_obs_emitter() -> Day0FastObsEmitter:
    """Process-wide emitter singleton (keeps the fetch throttle + move memo)."""
    global _EMITTER_SINGLETON
    with _EMITTER_LOCK:
        if _EMITTER_SINGLETON is None:
            _EMITTER_SINGLETON = Day0FastObsEmitter()
        return _EMITTER_SINGLETON
