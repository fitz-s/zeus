"""Gamma API market scanner: discover active weather markets.

Queries Polymarket's Gamma API for temperature events.
Parses bin structure, token IDs, and prices from market data.
"""

import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.config import City, cities_by_name

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Temperature keywords for event matching
TEMP_KEYWORDS = {"temperature", "highest temp", "°f", "°c", "fahrenheit", "celsius"}

_LOW_METRIC_KEYWORDS = (
    "lowest temperature",
    "low temperature",
    "lowest temp",
    "minimum temperature",
    "minimum temp",
    "min temperature",
    "daily low",
    "overnight low",
    "coldest temperature",
)

# Tag slugs to search (in priority order)
TAG_SLUGS = ["temperature", "weather", "daily-temperature"]
_ACTIVE_EVENTS_CACHE: list[dict] | None = None
_ACTIVE_EVENTS_CACHE_AT: float = 0.0  # monotonic timestamp of last fetch
_ACTIVE_EVENTS_TTL: float = 300.0  # 5-minute TTL


def infer_temperature_metric(*text_surfaces: str) -> str:
    """Infer market metric from free text.

    Returns:
        "low" when text clearly describes daily lows; otherwise "high".
    """
    text = " ".join(str(surface or "") for surface in text_surfaces).lower()
    if any(keyword in text for keyword in _LOW_METRIC_KEYWORDS):
        return "low"
    return "high"


def _gamma_get(path: str, *, params: dict | None = None, timeout: float = 15.0, retries: int = 3) -> httpx.Response:
    """GET a Gamma API path with retries on transient connection errors.

    The proxy path to gamma-api.polymarket.com periodically returns
    'Connection reset by peer' (errno 54). Retrying with a short backoff
    recovers reliably without masking real failures — after `retries`
    attempts the last exception propagates.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(f"{GAMMA_BASE}{path}", params=params, timeout=timeout)
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    assert last_exc is not None
    raise last_exc


def find_weather_markets(
    min_hours_to_resolution: float = 6.0,
) -> list[dict]:
    """Find active weather temperature markets. Spec §6.2.

    Returns list of enriched event dicts with parsed city, date, outcomes.
    """
    events = _get_active_events()
    if not events:
        events = _fetch_events_by_keyword("temperature")

    results = []
    now = datetime.now(timezone.utc)

    for event in events:
        parsed = _parse_event(event, now, min_hours_to_resolution)
        if parsed is not None:
            results.append(parsed)

    logger.info("Found %d active weather markets", len(results))
    return results


def get_current_yes_price(market_id: str) -> Optional[float]:
    """Fetch the current YES-side price for an active market via Gamma event data.

    Used during monitor cycles as the observable market price source when live
    CLOB VWMP is not available (e.g. non-CLOB positions).
    """
    events = _get_active_events()
    if not events:
        events = _fetch_events_by_keyword("temperature")

    for event in events:
        for outcome in _extract_outcomes(event):
            if outcome.get("market_id") == market_id:
                return float(outcome["price"])
    return None


def get_sibling_outcomes(market_id: str) -> list[dict]:
    """Return ALL outcomes (bins) for the event containing market_id.

    S6: needed by monitor_refresh to build the full bin vector for
    calibrate_and_normalize() (same path as entry).
    """
    events = _get_active_events()
    if not events:
        events = _fetch_events_by_keyword("temperature")

    for event in events:
        outcomes = _extract_outcomes(event)
        if any(o.get("market_id") == market_id for o in outcomes):
            return outcomes
    return []


def _get_active_events() -> list[dict]:
    global _ACTIVE_EVENTS_CACHE, _ACTIVE_EVENTS_CACHE_AT
    now = time.monotonic()
    if _ACTIVE_EVENTS_CACHE is None or (now - _ACTIVE_EVENTS_CACHE_AT) > _ACTIVE_EVENTS_TTL:
        _ACTIVE_EVENTS_CACHE = _fetch_events_by_tags()
        _ACTIVE_EVENTS_CACHE_AT = now
    return list(_ACTIVE_EVENTS_CACHE)


def _clear_active_events_cache() -> None:
    global _ACTIVE_EVENTS_CACHE, _ACTIVE_EVENTS_CACHE_AT
    _ACTIVE_EVENTS_CACHE = None
    _ACTIVE_EVENTS_CACHE_AT = 0.0


def _fetch_events_by_tags() -> list[dict]:
    """Fetch events using tag slugs."""
    for tag_slug in TAG_SLUGS:
        try:
            # Resolve tag ID
            resp = _gamma_get(f"/tags/slug/{tag_slug}")
            if resp.status_code != 200:
                continue
            tag_data = resp.json()
            tag_id = tag_data.get("id")
            if not tag_id:
                continue

            # Fetch events with this tag
            events = []
            offset = 0
            while True:
                resp = _gamma_get("/events", params={
                    "tag_id": tag_id, "closed": "false", "limit": 50, "offset": offset
                })
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                events.extend(batch)
                if len(batch) < 50:
                    break
                offset += 50

            if events:
                return events
        except httpx.HTTPError as e:
            logger.warning("Tag fetch failed for %s: %s", tag_slug, e)
            continue

    return []


def _fetch_events_by_keyword(keyword: str) -> list[dict]:
    """Fallback: fetch events by keyword search."""
    try:
        resp = _gamma_get("/events", params={
            "closed": "false", "limit": 100, "title": keyword
        })
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        logger.warning("Keyword fetch failed: %s", e)
        return []


def _parse_event(
    event: dict,
    now: datetime,
    min_hours: float,
) -> Optional[dict]:
    """Parse a Gamma event into Zeus format. Returns None if not a valid weather market."""
    title = (event.get("title") or "").lower()

    # Must be a temperature event
    if not any(kw in title for kw in TEMP_KEYWORDS):
        return None

    # Match city
    city = _match_city(title, event.get("slug", ""))
    if city is None:
        return None
    sanity_rejection = _market_city_sanity_rejection(event, city)
    if sanity_rejection is not None:
        logger.warning(
            "Rejecting Gamma market city mismatch: city=%s reason=%s event=%s",
            city.name,
            sanity_rejection,
            event.get("id") or event.get("slug"),
        )
        return None

    # Parse target date from slug or end date
    target_date = _parse_target_date(event)
    if target_date is None:
        return None

    # Check time to resolution
    end_str = event.get("endDate") or event.get("end_date")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            hours_to_resolution = (end_dt - now).total_seconds() / 3600
            if hours_to_resolution < min_hours:
                return None
        except (ValueError, TypeError):
            logger.warning(
                "Unparseable endDate %r for event %s — skipping market",
                end_str,
                event.get("id") or event.get("slug"),
            )
            return None
    else:
        hours_to_resolution = 24.0

    # Extract bin structure from markets
    outcomes = _extract_outcomes(event)
    if not outcomes:
        return None

    metric_surfaces = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        metric_surfaces.extend(
            [
                market.get("question", ""),
                market.get("title", ""),
                market.get("description", ""),
                market.get("groupItemTitle", ""),
                market.get("group_item_title", ""),
            ]
        )
    temperature_metric = infer_temperature_metric(*metric_surfaces)

    # Compute hours since market opened
    created_str = event.get("createdAt") or event.get("created_at")
    hours_since_open = 24.0
    if created_str:
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            hours_since_open = (now - created).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

    return {
        "event_id": event.get("id") or event.get("slug"),
        "slug": event.get("slug", ""),
        "title": event.get("title", ""),
        "city": city,
        "target_date": target_date,
        "temperature_metric": temperature_metric,
        "hours_to_resolution": hours_to_resolution,
        "hours_since_open": hours_since_open,
        "outcomes": outcomes,
    }


def _match_city(title: str, slug: str) -> Optional[City]:
    """Match event title/slug to a configured city using aliases from cities.json."""
    from src.config import cities

    text = f"{title} {slug}".lower()
    slug_text = slug.lower()

    # Use boundary-aware aliases. Short aliases such as "LA" and "SF" must not
    # match inside longer city names like "Kuala Lumpur" or unrelated words.
    candidates: list[tuple[str, City, str]] = []
    for city in cities:
        candidates.extend((alias.lower(), city, "text") for alias in city.aliases)
        candidates.extend((slug_name.lower(), city, "slug") for slug_name in city.slug_names)

    for alias, city, surface in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        haystack = slug_text if surface == "slug" else text
        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
        if re.search(pattern, haystack):
            return city

    return None


def _city_match_tokens(city: City) -> set[str]:
    tokens = {
        city.name,
        city.wu_station,
        city.airport_name,
        city.settlement_source,
        *city.aliases,
        *city.slug_names,
    }
    return {str(token).strip().lower() for token in tokens if str(token).strip()}


def _token_in_text(token: str, text: str) -> bool:
    if not token:
        return False
    normalized = token.lower()
    if "/" in normalized or "." in normalized:
        return normalized in text
    if "-" in normalized:
        return normalized in text or normalized.replace("-", " ") in text
    pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _market_city_sanity_rejection(event: dict, matched_city: City) -> str | None:
    """Reject Gamma events that explicitly identify a different configured city."""
    from src.config import cities

    text_fields = [
        event.get("title", ""),
        event.get("slug", ""),
        event.get("description", ""),
        event.get("resolutionSource", ""),
        event.get("resolution_source", ""),
        event.get("groupItemTitle", ""),
        event.get("group_item_title", ""),
    ]
    for market in event.get("markets", []) or []:
        text_fields.extend([
            market.get("question", ""),
            market.get("slug", ""),
            market.get("description", ""),
            market.get("resolutionSource", ""),
            market.get("resolution_source", ""),
            market.get("groupItemTitle", ""),
            market.get("group_item_title", ""),
        ])
    combined = " ".join(str(field) for field in text_fields if field).lower()
    if not combined:
        return None

    matched_tokens = _city_match_tokens(matched_city)
    for city in cities:
        if city.name == matched_city.name:
            continue
        for token in sorted(_city_match_tokens(city), key=len, reverse=True):
            if token in matched_tokens:
                continue
            if _token_in_text(token, combined):
                return f"matched {matched_city.name} but text references {city.name} via {token!r}"
    return None


def _parse_target_date(event: dict) -> Optional[str]:
    """Extract target date from event slug or end date."""
    slug = event.get("slug", "")

    # Try slug pattern: highest-temperature-in-{city}-on-{month}-{day}-{year}
    m = re.search(r"on-(\w+)-(\d+)-(\d{4})", slug)
    if m:
        month_name, day, year = m.group(1), m.group(2), m.group(3)
        try:
            from datetime import datetime as dt
            parsed = dt.strptime(f"{month_name} {day} {year}", "%B %d %Y")
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Fallback: use end date
    end_str = event.get("endDate") or event.get("end_date")
    if end_str:
        try:
            return end_str[:10]  # YYYY-MM-DD
        except (IndexError, TypeError):
            pass

    return None


def _extract_outcomes(event: dict) -> list[dict]:
    """Extract bin outcomes from event markets."""
    outcomes = []
    markets = event.get("markets", [])

    for market in markets:
        question = market.get("question", "")

        # Parse token IDs — may be JSON string or list
        clob_tokens = market.get("clobTokenIds", "[]")
        if isinstance(clob_tokens, str):
            try:
                clob_tokens = json.loads(clob_tokens)
            except (json.JSONDecodeError, TypeError):
                continue
        if not clob_tokens or len(clob_tokens) < 2:
            continue

        yes_token = clob_tokens[0]
        no_token = clob_tokens[1]

        # K1/#43: Validate token→outcome label mapping instead of assuming
        # positional order.  Polymarket markets carry an "outcomes" list
        # (e.g. ["Yes", "No"]) whose indices correspond to clobTokenIds.
        outcome_labels = market.get("outcomes", "[]")
        if isinstance(outcome_labels, str):
            try:
                outcome_labels = json.loads(outcome_labels)
            except (json.JSONDecodeError, TypeError):
                outcome_labels = []
        if len(outcome_labels) >= 2:
            label_0 = str(outcome_labels[0]).strip().lower()
            label_1 = str(outcome_labels[1]).strip().lower()
            if label_0 == "no" and label_1 == "yes":
                # Tokens are reversed vs our assumption — swap.
                yes_token, no_token = no_token, yes_token
                _labels_swapped = True
            elif label_0 != "yes" or label_1 != "no":
                # Unrecognised outcome labels — skip this market.
                continue
            else:
                _labels_swapped = False
        else:
            _labels_swapped = False

        # Parse prices — may be JSON string or list
        prices = market.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except (json.JSONDecodeError, TypeError):
                logger.warning("outcomePrices parse failed for market %s, skipping",
                               market.get("questionID", "?"))
                continue
        if len(prices) < 2:
            logger.warning("outcomePrices has < 2 elements for market %s, skipping",
                           market.get("questionID", "?"))
            continue
        yes_price = float(prices[0])
        no_price = float(prices[1])
        if _labels_swapped:
            yes_price, no_price = no_price, yes_price

        # Parse range from question text
        range_low, range_high = _parse_temp_range(question)

        outcomes.append({
            "title": question,
            "token_id": yes_token,
            "no_token_id": no_token,
            "price": yes_price,
            "no_price": no_price,
            "range_low": range_low,
            "range_high": range_high,
            "market_id": market.get("conditionId") or market.get("id", ""),
        })

    return outcomes


def _parse_temp_range(question: str) -> tuple[Optional[float], Optional[float]]:
    """Parse temperature range from market question text.

    Returns (range_low, range_high). None for open-ended.
    """
    q = question.strip()

    # "X-Y°F" or "X-Y °F" or "X–Y°F" (en-dash)
    m = re.search(r"(-?\d+\.?\d*)\s*[-–]\s*(-?\d+\.?\d*)\s*°[FfCc]", q)
    if m:
        return float(m.group(1)), float(m.group(2))

    # "X°F or below" / "X°C or below" / "X°F or lower"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(below|lower)", q)
    if m:
        return None, float(m.group(1))

    # "X°F or higher" / "X°C or higher" / "X°F or above"
    m = re.search(r"(-?\d+\.?\d*)\s*°[FfCc]\s+or\s+(higher|above|more)", q)
    if m:
        return float(m.group(1)), None

    # "X°C" single degree
    m = re.search(r"(-?\d+\.?\d*)\s*°[Cc]$", q)
    if m:
        val = float(m.group(1))
        return val, val

    return None, None
