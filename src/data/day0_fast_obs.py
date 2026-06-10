# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: day0 first-principles review 2026-06-10 §6.2 (live obs hook)
#   + operator green-light 2026-06-10 (free METAR fast lane; no paid sources);
#   /tmp/weather_source_research.md (aviationweather.gov ~3-5 min obs-to-cache,
#   verified live 2026-06-10: KLGA 3.3 min, RKSI 4.6 min, EGLC 5.5 min).
"""Day0 fast observation lane: free METAR feed for the running-extreme tracker.

First principles
----------------
The day0 absorbing boundary is driven by the settlement station's running
extreme. WU (the Polymarket settlement reference) publishes the SAME
ASOS/METAR stream with 11-37 min median delay, and Zeus's persisted entry-lane
surface adds another hourly import grid on top (measured 50-136 min median —
see config/wu_obs_latency.json). aviationweather.gov serves the same station
reports ~3-5 min after observation, free, no key, global coverage. This module
reads that feed and emits DAY0_EXTREME_UPDATED events the moment the running
extreme MOVES — the live hook the 2026-06-10 review found had zero callers.

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
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

UTC = timezone.utc

AVIATIONWEATHER_METAR_ENDPOINT = "https://aviationweather.gov/api/data/metar"

#: Canonical source id carried in event payload provenance.
FAST_OBS_SOURCE_ID = "aviationweather_metar"

#: T-group (temperature to tenths C) presence in the raw METAR remarks,
#: e.g. "T02110150". Required for F-settled extreme tracking (see module doc).
_T_GROUP_RE = re.compile(r"\bT\d{8}\b")

#: Minimum seconds between live HTTP fetches (the AWC cache updates ~1/min;
#: the reactor cycle can be faster — do not hammer a free government API).
DEFAULT_MIN_FETCH_INTERVAL_S = 90.0


@dataclass(frozen=True)
class FastObsSource:
    """Per-city fast-lane source descriptor (the source registry entry)."""

    source_id: str
    station_id: str
    authority: str  # provenance authority class for the stream
    notes: str = ""


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
        # SETTLEMENT-FAITHFULNESS GATE (operator correction 2026-06-10,
        # measured config/wu_metar_divergence.json): a station whose METAR
        # integer is NOT reliably WU's settlement integer (Seoul/RKSI class:
        # +-1C on ~4.5% of reports) must not have METAR drive bin-kill
        # decisions. Excluding the city is the monotone-safe direction —
        # absence of fast events never kills a bin; the slower WU-derived
        # lanes still serve it. Lazy import avoids a module cycle.
        try:
            from src.data.day0_oracle_anomaly import city_metar_settlement_faithful

            if not city_metar_settlement_faithful(str(getattr(city, "name", "") or "")):
                logger.warning(
                    "DAY0_FAST_OBS_CITY_EXCLUDED city=%s station=%s reason=metar_not_settlement_faithful "
                    "(measured WU-vs-METAR divergence; see config/wu_metar_divergence.json)",
                    getattr(city, "name", "?"), station,
                )
                return None
        except ImportError:
            pass  # faithfulness model unavailable -> registry behaves as before
        return FastObsSource(
            source_id=FAST_OBS_SOURCE_ID,
            station_id=station,
            authority="ICAO_STATION_NATIVE",
            notes="same physical settlement station as WU; NOAA AWC distribution",
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


def fetch_metar_reports(
    stations: Iterable[str],
    *,
    hours: float = 36.0,
    timeout: float = 15.0,
    endpoint: str = AVIATIONWEATHER_METAR_ENDPOINT,
) -> list[MetarReport]:
    """One batched fetch for all stations. Fail-soft: any error returns []."""
    ids = ",".join(sorted({str(s).strip().upper() for s in stations if str(s).strip()}))
    if not ids:
        return []
    try:
        resp = httpx.get(
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


def running_extremes_for_local_day(
    reports: Iterable[MetarReport],
    *,
    city: Any,
    target_date: date | str,
    as_of: Optional[datetime] = None,
) -> FastObsExtremes:
    """Running extremes over the city-local target day from METAR reports.

    Local-day membership via ZoneInfo on the report obs time (DST-correct).
    ``as_of`` truncates samples at/before that UTC instant — used by the
    oracle-anomaly detector to compare against a slower WU snapshot over the
    SAME observation window.
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
    if not values:
        return FastObsExtremes(
            city=str(getattr(city, "name", "")), station_id=station,
            target_date=target.isoformat(), unit=unit,
            high_so_far=None, low_so_far=None, current_temp=None,
            first_obs_time=None, last_obs_time=None, last_receipt_time=None,
            sample_count=0, skipped_unit_law=skipped,
        )
    temps = [v for _, v, _ in values]
    receipts = [r for _, _, r in values if r is not None]
    return FastObsExtremes(
        city=str(getattr(city, "name", "")), station_id=station,
        target_date=target.isoformat(), unit=unit,
        high_so_far=max(temps), low_so_far=min(temps), current_temp=temps[-1],
        first_obs_time=values[0][0], last_obs_time=values[-1][0],
        last_receipt_time=max(receipts) if receipts else None,
        sample_count=len(values), skipped_unit_law=skipped,
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
    available_at = (
        extremes.last_receipt_time.astimezone(UTC).isoformat()
        if extremes.last_receipt_time is not None
        else datetime.now(UTC).isoformat()
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
        "LIVE_AUTHORITY"
        if (
            source_authorized == "AUTHORIZED"
            and local_date_status == "MATCH"
            and dst_status == "UNAMBIGUOUS"
        )
        else "NON_LIVE_AUTHORITY"
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
    }


@dataclass
class Day0FastObsEmitter:
    """Stateful fast-lane emitter: fetch -> extremes -> emit-on-boundary-move.

    Emission policy is MONOTONE: a (city, date, metric) emits only when the
    rounded running extreme moves in the absorbing direction (high: up,
    low: down) or on first sight. Re-emissions of the same report dedup at the
    event store via the idempotency key (available_at = feed receiptTime).
    In-process memo only — a daemon restart re-emits once and dedups.
    """

    fetcher: Callable[..., list[MetarReport]] = fetch_metar_reports
    min_fetch_interval_s: float = DEFAULT_MIN_FETCH_INTERVAL_S
    _last_fetch_monotonic: float = field(default=0.0, init=False)
    _cached_reports: list[MetarReport] = field(default_factory=list, init=False)
    _last_emitted_rounded: dict[tuple[str, str, str], int] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def _reports(self, stations: list[str]) -> list[MetarReport]:
        now = time.monotonic()
        with self._lock:
            if self._cached_reports and (now - self._last_fetch_monotonic) < self.min_fetch_interval_s:
                return list(self._cached_reports)
        reports = self.fetcher(stations)
        with self._lock:
            if reports:
                self._cached_reports = list(reports)
                self._last_fetch_monotonic = now
            return list(self._cached_reports)

    def emit_events(
        self,
        *,
        world_conn,
        cities: list[Any],
        decision_time: datetime,
        received_at: str,
        limit: int = 50,
        anomaly_check: Optional[Callable[[Any, FastObsExtremes, list[MetarReport]], None]] = None,
    ) -> int:
        """Emit DAY0_EXTREME_UPDATED events from the fast METAR lane.

        cities: runtime City objects. Only cities with a fast-lane source AND a
        day0 target (local today at decision_time) are polled. Fail-soft per
        city; fail-closed per field (hard-fact statuses).
        """
        from src.events.event_writer import EventWriter
        from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger
        from src.contracts.settlement_semantics import SettlementSemantics

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
            return 0

        reports = self._reports([source.station_id for _, source, _ in eligible])
        if not reports:
            return 0

        trigger = Day0ExtremeUpdatedTrigger(EventWriter(world_conn))
        emitted = 0
        for city, source, target_date in eligible:
            if emitted >= max(1, int(limit)):
                break
            try:
                extremes = running_extremes_for_local_day(
                    reports, city=city, target_date=target_date, as_of=decision_time.astimezone(UTC)
                )
                if extremes.sample_count == 0:
                    continue
                if anomaly_check is not None:
                    try:
                        anomaly_check(city, extremes, reports)
                    except Exception as exc:  # noqa: BLE001 — detector must never block emission
                        logger.warning(
                            "DAY0_FAST_OBS_ANOMALY_CHECK_FAILED city=%s exc=%s: %s",
                            getattr(city, "name", "?"), type(exc).__name__, exc,
                        )
                semantics = SettlementSemantics.for_city(city)
                for metric in ("high", "low"):
                    value = extremes.high_so_far if metric == "high" else extremes.low_so_far
                    if value is None:
                        continue
                    rounded = int(semantics.round_single(float(value)))
                    key = (str(getattr(city, "name", "")), target_date, metric)
                    previous = self._last_emitted_rounded.get(key)
                    moved = (
                        previous is None
                        or (metric == "high" and rounded > previous)
                        or (metric == "low" and rounded < previous)
                    )
                    if not moved:
                        continue
                    observation = fast_obs_to_day0_observation(
                        city=city, extremes=extremes, metric=metric, source=source
                    )
                    result = trigger.emit_from_observation(
                        observation=observation,
                        settlement_semantics=semantics,
                        decision_time=decision_time,
                        received_at=received_at,
                    )
                    self._last_emitted_rounded[key] = rounded
                    if result.inserted:
                        emitted += 1
                        logger.info(
                            "DAY0_FAST_OBS_EMIT city=%s date=%s metric=%s rounded=%s "
                            "obs_time=%s available_at=%s samples=%d skipped_unit_law=%d",
                            key[0], target_date, metric, rounded,
                            observation["observation_time"], observation["observation_available_at"],
                            extremes.sample_count, extremes.skipped_unit_law,
                        )
            except Exception as exc:  # noqa: BLE001 — one city must not kill the lane
                logger.warning(
                    "DAY0_FAST_OBS_CITY_FAILED city=%s exc=%s: %s",
                    getattr(city, "name", "?"), type(exc).__name__, exc,
                )
        return emitted


_EMITTER_SINGLETON: Day0FastObsEmitter | None = None
_EMITTER_LOCK = threading.Lock()


def get_fast_obs_emitter() -> Day0FastObsEmitter:
    """Process-wide emitter singleton (keeps the fetch throttle + move memo)."""
    global _EMITTER_SINGLETON
    with _EMITTER_LOCK:
        if _EMITTER_SINGLETON is None:
            _EMITTER_SINGLETON = Day0FastObsEmitter()
        return _EMITTER_SINGLETON
