# Created: 2026-09-12
# Last audited: 2026-09-12
# Authority basis: docs/operations/current/noaa_settlement_page_truth/{PLAN.md,evidence.md};
#   architecture/city_truth_contract.yaml NOAA rows; AGENTS.md §2 settlement law.
"""The settlement product for NOAA cities: the feed behind weather.gov/wrh/timeseries.

Why this exists
---------------
Polymarket's 48 NOAA cities resolve off the table rendered at
``https://www.weather.gov/wrh/timeseries?site=<ICAO>``. Zeus previously
reconstructed that number from Ogimet's whole-degree METAR bodies, which is a
different quantity: measured 2026-09-12, the Ogimet reconstruction disagrees
with the chain-winning bin on 15-37% of days for the 11 US degF cities (Houston
2026-09-11: ours 93 degF, chain 94-95 degF). This module reads the page's own
data so labels, disputes and Day0 finality are computed on the contract's law
rather than on a proxy for it.

Measured facts (verified 2026-09-12, cite before changing any of this)
---------------------------------------------------------------------
The page is a client-side render; its script ``/source/wrh/timeseries/obs.js``
calls

    https://api.synopticdata.com/v2/stations/timeseries
        ?STID=<ICAO>&showemptystations=1[&units=temp|F,speed|kts,english]
        &recent=<minutes> | &start=YYYYMMDDHHMM&end=YYYYMMDDHHMM
        &complete=1&token=<mesoToken>&obtimezone=local

with ``units=temp|F,...`` for the "US Units" view (degF cities) and no units
parameter for the metric view (degC cities). ``mesoToken`` is read at runtime
from ``https://www.weather.gov/source/wrh/apiKey.js`` (body:
``var mesoToken='<hex>';``); it is never hardcoded here.

Request shape is load-bearing. A paced test at 20:49-20:52Z on 2026-09-12
returned 200 for ``recent=120``, a one-day start/end window, ``recent=4320``
(941 rows) and a seven-day start/end window (2053 rows) when the request
carried ``Referer: https://www.weather.gov/wrh/timeseries?site=<STID>``,
``Origin: https://www.weather.gov`` and a browser-like User-Agent; the same
requests with no headers returned HTTP 403
``{"SUMMARY":{"RESPONSE_MESSAGE":"Invalid request per token rules"}}``. A
separate burst of ~17 twenty-two-day history pulls between 20:17Z and 20:24Z
tripped a short per-IP quota that refused every subsequent request for roughly
ten minutes, including correctly-headed ones. Both facts set the policy below:
always send the page's headers, and stay sparse — one ``recent=`` window per
station per day for the daily product, at most seven days per request in the
backfill CLI, with ``_MIN_REQUEST_INTERVAL_SECONDS`` between requests. A 403 is
a typed :class:`WrhTokenRefused`, never "no data": the caller writes nothing and
the settlement writer's existing no-observation path keeps the row DISPUTED.

Page render law (read from obs.js, 2026-09-12)
----------------------------------------------
Each shown row's Temperature cell is ``Math.round(air_temp_set_1[j])``. With
``hourly=true`` (the "Show Hourly Data" button) and an ASOS/AWOS station, a row
is shown iff ``sea_level_pressure_set_1[j] !== null`` (a routine hourly METAR)
or ``metar_set_1[j].toUpperCase().startsWith(SITE)`` (a SPECI, highlighted).
With ``hourly=false`` every row is shown. The market's daily value is the max
(for "highest") or min (for "lowest") over shown rows whose LOCAL date equals
the target date, then rounded. ``Math.round(x) == floor(x + 0.5)`` for every
real x, which is exactly the repo's ``WMO_HalfUp`` policy in
``src/contracts/settlement_semantics.py`` — callers round through that, and this
module returns the raw extremum so the rounding law stays in one place.

Which view applies is stated in the market description and carried per city by
``City.settlement_page_view``: the 11 US degF cities say "This market will
resolve off of the Hourly Data provided using the \"Show Hourly Data\" button."
and the other 37 NOAA cities do not. Replayed against chain-winning bins for
2026-08-23..2026-09-11: the hourly view matches 434/434 settled degF cells,
while the all-data view matches only 302/434.

The feed's value is the product; the raw METAR text is not
--------------------------------------------------------
``air_temp_set_1`` on a SPECI row is sometimes the body integer even when the
report text carries a tenths-precision T-group: of 563 shown SPECI rows in the
degF-city window, 70 carry a T-group that disagrees with the feed's own value,
apparently a first-transmission decode that later text revisions do not
correct. The page shows, and the chain resolves, the feed value. So this module
stores ``air_temp_set_1`` verbatim and keeps ``metar_set_1`` in provenance only;
re-deriving the temperature from the METAR text would reintroduce the very
disagreement this module removes.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

import httpx

logger = logging.getLogger(__name__)

WRH_API_KEY_URL = "https://www.weather.gov/source/wrh/apiKey.js"
WRH_TIMESERIES_URL = "https://api.synopticdata.com/v2/stations/timeseries"
WRH_PAGE_URL = "https://www.weather.gov/wrh/timeseries"

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

#: Minimum spacing between outgoing Synoptic requests, enforced in-process.
#: A burst of large history pulls tripped a per-IP quota on 2026-09-12; every
#: caller pays this interval so one backfill cannot starve the daily tick.
_MIN_REQUEST_INTERVAL_SECONDS = 2.0

#: Bounded retry for transport faults and 5xx only. A 403 is never retried —
#: it means the token/header contract or the per-IP quota, not a flaky hop.
_RETRY_COUNT = 2
_RETRY_BACKOFF_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 45.0

#: Largest window one request may ask for. The page itself pulls seven days;
#: bigger sweeps are what tripped the quota.
MAX_REQUEST_WINDOW_DAYS = 7

_TOKEN_RE = re.compile(r"mesoToken\s*=\s*['\"]([0-9a-fA-F]{8,})['\"]")

_request_lock = threading.Lock()
_last_request_at: float = 0.0

_token_cache: Optional[str] = None
_token_fetched_at: Optional[datetime] = None
_token_lock = threading.Lock()

PageView = Literal["hourly", "all"]
Metric = Literal["high", "low"]
Unit = Literal["F", "C"]


class WrhError(RuntimeError):
    """Base class for weather.gov timeseries product failures."""


class WrhTokenUnavailable(WrhError):
    """apiKey.js did not yield a mesoToken."""


class WrhTokenRefused(WrhError):
    """Synoptic answered 403 'Invalid request per token rules'.

    Distinct from an empty response on purpose: a refused request proves
    nothing about the station, so the caller must write nothing rather than
    record a station-dark day.
    """


class WrhFetchFailed(WrhError):
    """Transport fault or non-200, non-403 status after bounded retry."""


@dataclass(frozen=True)
class WrhRow:
    """One row of the page's feed, as the page itself would read it."""

    local_timestamp: str
    """``date_time`` verbatim, e.g. ``2026-09-11T15:51:00-0400``."""

    utc: datetime
    air_temp: float
    """``air_temp_set_1``: degF when the request asked for degF, else degC."""

    is_routine_metar: bool
    """``sea_level_pressure_set_1`` is non-null: a routine hourly METAR."""

    is_official_report: bool
    """Routine, or a SPECI whose METAR text starts with the station id."""

    raw_metar: Optional[str]

    @property
    def local_date(self) -> str:
        return self.local_timestamp[:10]


@dataclass(frozen=True)
class WrhExtreme:
    """The page's daily value for one (date, view, metric), before rounding."""

    value: float
    local_timestamp: str
    raw_metar: Optional[str]
    n_rows: int
    """Rows the view showed for this local date."""

    n_official: int
    """Of those, rows that are routine METAR or station-prefixed SPECI."""


def _wait_for_request_slot() -> None:
    global _last_request_at
    with _request_lock:
        remaining = _MIN_REQUEST_INTERVAL_SECONDS - (
            time.monotonic() - _last_request_at
        )
        if remaining > 0:
            time.sleep(remaining)
        _last_request_at = time.monotonic()


def _page_headers(station: str) -> dict[str, str]:
    """The exact header set obs.js's cross-origin request carries.

    Measured 2026-09-12: without Referer + Origin the API answers 403 for every
    window size, with them it answers 200 for the same token.
    """
    return {
        "User-Agent": _BROWSER_USER_AGENT,
        "Referer": f"{WRH_PAGE_URL}?site={station.upper()}",
        "Origin": "https://www.weather.gov",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }


def fetch_wrh_token(*, refresh: bool = False) -> str:
    """Return the page's mesoToken, fetched once per process.

    The token rotates upstream, so it is read from apiKey.js at runtime and
    cached for the run only. ``refresh=True`` forces a re-read.
    """
    global _token_cache, _token_fetched_at
    with _token_lock:
        if _token_cache and not refresh:
            return _token_cache
        try:
            response = httpx.get(
                WRH_API_KEY_URL,
                headers={"User-Agent": _BROWSER_USER_AGENT},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise WrhTokenUnavailable(f"apiKey.js fetch failed: {exc}") from exc
        if response.status_code != 200:
            raise WrhTokenUnavailable(
                f"apiKey.js returned HTTP {response.status_code}"
            )
        match = _TOKEN_RE.search(response.text)
        if not match:
            raise WrhTokenUnavailable(
                "apiKey.js carried no mesoToken assignment"
            )
        _token_cache = match.group(1)
        _token_fetched_at = datetime.now(timezone.utc)
        return _token_cache


def token_fetched_at() -> Optional[datetime]:
    """When the cached token was read, for provenance stamping."""
    return _token_fetched_at


def request_url_without_token(
    station: str,
    *,
    unit: Unit,
    start_utc: Optional[datetime] = None,
    end_utc: Optional[datetime] = None,
    recent_minutes: Optional[int] = None,
) -> str:
    """The request URL with the token elided, safe to persist in provenance."""
    params = _query_params(
        station,
        unit=unit,
        start_utc=start_utc,
        end_utc=end_utc,
        recent_minutes=recent_minutes,
        token="REDACTED",
    )
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{WRH_TIMESERIES_URL}?{query}"


def _query_params(
    station: str,
    *,
    unit: Unit,
    start_utc: Optional[datetime],
    end_utc: Optional[datetime],
    recent_minutes: Optional[int],
    token: str,
) -> dict[str, str]:
    params: dict[str, str] = {
        "STID": station.upper(),
        "showemptystations": "1",
    }
    # The degF view is a units parameter on the same endpoint; the metric view
    # sends none at all. Anything else would be a third view the page has not.
    if unit == "F":
        params["units"] = "temp|F,speed|kts,english"
    if recent_minutes is not None:
        params["recent"] = str(int(recent_minutes))
    else:
        assert start_utc is not None and end_utc is not None
        params["start"] = start_utc.strftime("%Y%m%d%H%M")
        params["end"] = end_utc.strftime("%Y%m%d%H%M")
    params["complete"] = "1"
    params["token"] = token
    params["obtimezone"] = "local"
    return params


def _parse_rows(payload: dict, station: str) -> list[WrhRow]:
    stations = payload.get("STATION") or []
    if not stations:
        return []
    observations = stations[0].get("OBSERVATIONS") or {}
    timestamps = observations.get("date_time") or []
    temps = observations.get("air_temp_set_1") or []
    pressures = observations.get("sea_level_pressure_set_1") or [None] * len(timestamps)
    metars = observations.get("metar_set_1") or [None] * len(timestamps)
    prefix = station.upper()

    rows: list[WrhRow] = []
    for index, local_timestamp in enumerate(timestamps):
        if index >= len(temps) or temps[index] is None:
            continue
        try:
            utc = datetime.strptime(
                local_timestamp, "%Y-%m-%dT%H:%M:%S%z"
            ).astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
        raw_metar = metars[index] if index < len(metars) else None
        routine = index < len(pressures) and pressures[index] is not None
        speci = bool(raw_metar) and str(raw_metar).upper().startswith(prefix)
        rows.append(
            WrhRow(
                local_timestamp=str(local_timestamp),
                utc=utc,
                air_temp=float(temps[index]),
                is_routine_metar=routine,
                is_official_report=routine or speci,
                raw_metar=str(raw_metar) if raw_metar is not None else None,
            )
        )
    return rows


def fetch_wrh_timeseries(
    station: str,
    start_utc: Optional[datetime] = None,
    end_utc: Optional[datetime] = None,
    *,
    unit: Unit,
    token: str,
    recent_minutes: Optional[int] = None,
) -> list[WrhRow]:
    """Fetch one window of the page's feed.

    Pass ``recent_minutes`` for the daily product (one small window per station
    per day, the shape the page itself uses) or ``start_utc``/``end_utc`` for
    the backfill. Windows are capped at :data:`MAX_REQUEST_WINDOW_DAYS`; the
    caller filters by local date from the returned local timestamps rather than
    trusting the endpoint's window-edge timezone semantics, so a start/end
    caller should widen its window by a day on each side.
    """
    if recent_minutes is None and (start_utc is None or end_utc is None):
        raise ValueError(
            "fetch_wrh_timeseries needs recent_minutes or start_utc+end_utc"
        )
    if recent_minutes is not None:
        if recent_minutes <= 0:
            raise ValueError("recent_minutes must be positive")
        if recent_minutes > MAX_REQUEST_WINDOW_DAYS * 24 * 60:
            raise ValueError(
                f"recent_minutes {recent_minutes} exceeds the "
                f"{MAX_REQUEST_WINDOW_DAYS}-day request cap"
            )
    elif end_utc - start_utc > timedelta(days=MAX_REQUEST_WINDOW_DAYS):
        raise ValueError(
            f"window {start_utc.isoformat()}..{end_utc.isoformat()} exceeds the "
            f"{MAX_REQUEST_WINDOW_DAYS}-day request cap"
        )

    params = _query_params(
        station,
        unit=unit,
        start_utc=start_utc,
        end_utc=end_utc,
        recent_minutes=recent_minutes,
        token=token,
    )
    headers = _page_headers(station)
    last_error = ""
    for attempt in range(_RETRY_COUNT + 1):
        _wait_for_request_slot()
        try:
            response = httpx.get(
                WRH_TIMESERIES_URL,
                params=params,
                headers=headers,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            last_error = str(exc)
            logger.warning(
                "wrh timeseries %s transport error (attempt %d/%d): %s",
                station, attempt + 1, _RETRY_COUNT + 1, exc,
            )
        else:
            if response.status_code == 403:
                raise WrhTokenRefused(
                    f"{station}: Synoptic refused the request (HTTP 403): "
                    f"{response.text[:200]}"
                )
            if response.status_code == 200:
                return _parse_rows(response.json(), station)
            last_error = f"HTTP {response.status_code}"
            if response.status_code < 500:
                raise WrhFetchFailed(f"{station}: {last_error}")
            logger.warning(
                "wrh timeseries %s HTTP %d (attempt %d/%d)",
                station, response.status_code, attempt + 1, _RETRY_COUNT + 1,
            )
        if attempt < _RETRY_COUNT:
            time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise WrhFetchFailed(f"{station}: {last_error or 'no response'}")


class WrhWindowTooOld(WrhError):
    """The target local day cannot fit in one ``recent=`` request.

    A window that starts after the target day began would still return rows, and
    an extremum over that subset looks exactly like a complete day. Making this
    a typed refusal is the only way the caller can tell "the whole day arrived"
    from "the tail of the day arrived": both produce a non-empty row set.
    """


def recent_minutes_for_local_day(
    target_date_local: date,
    timezone_name: str,
    *,
    now_utc: Optional[datetime] = None,
    margin_minutes: int = 180,
) -> int:
    """Minutes of ``recent=`` that cover a whole local day plus a margin.

    The daily product runs after local midnight, so one window reaching back to
    the start of the target local day (plus a margin for the next day's first
    reports, which the contract's finality clause keys on) is the entire request
    budget for that station that day.

    Raises :class:`WrhWindowTooOld` when the needed window exceeds
    :data:`MAX_REQUEST_WINDOW_DAYS`. Clamping instead would silently return a
    window that starts mid-day: measured on the KHOU feed, a target date seven
    days old yields a window missing the true 05:53 minimum, so the day's low
    reads 80.96 degF instead of 78.98 and would be written VERIFIED. A caller
    that needs an older day must use the explicit ``start_utc``/``end_utc``
    form, which is what the backfill CLI does.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name)
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    day_start_local = datetime(
        target_date_local.year,
        target_date_local.month,
        target_date_local.day,
        tzinfo=tz,
    )
    elapsed = now_utc - day_start_local.astimezone(timezone.utc)
    minutes = int(elapsed.total_seconds() // 60) + margin_minutes
    cap = MAX_REQUEST_WINDOW_DAYS * 24 * 60
    if minutes > cap:
        raise WrhWindowTooOld(
            f"{target_date_local.isoformat()} needs a {minutes}-minute window, "
            f"over the {cap}-minute request cap; a clamped window would not "
            "contain the whole local day"
        )
    return max(1, minutes)


def daily_extreme(
    rows: list[WrhRow],
    *,
    target_date_local: date | str,
    view: PageView,
    metric: Metric,
) -> Optional[WrhExtreme]:
    """The page's daily value for one local date, unrounded.

    Applies the render law: the hourly view keeps official reports only, the
    all-data view keeps every row. Returns ``None`` when the view shows no row
    for that local date (the station was dark) so the caller writes nothing and
    the settlement writer's no-observation path keeps the row DISPUTED. That is
    also the class the contract's no-data clause resolves to the lowest bracket,
    which Zeus must never reproduce by guessing.
    """
    if view not in ("hourly", "all"):
        raise ValueError(f"unknown page view {view!r}")
    if metric not in ("high", "low"):
        raise ValueError(f"unknown metric {metric!r}")
    wanted = (
        target_date_local
        if isinstance(target_date_local, str)
        else target_date_local.isoformat()
    )

    shown = [
        row
        for row in rows
        if row.local_date == wanted
        and (view == "all" or row.is_official_report)
    ]
    if not shown:
        return None

    pick = max if metric == "high" else min
    extreme = pick(shown, key=lambda row: row.air_temp)
    return WrhExtreme(
        value=extreme.air_temp,
        local_timestamp=extreme.local_timestamp,
        raw_metar=extreme.raw_metar,
        n_rows=len(shown),
        n_official=sum(1 for row in shown if row.is_official_report),
    )


def rows_from_payload(payload: dict, station: str) -> list[WrhRow]:
    """Parse a saved response body. Fixture and offline-replay entry point."""
    return _parse_rows(payload, station)
