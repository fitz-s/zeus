#!/usr/bin/env python3
"""
Weekly city settlement audit for Zeus weather markets on Polymarket.

Checks (per city in cities.json):
  1. Unit consistency (°C vs °F) — from Polymarket market description
  2. Settlement source (WU URL, NOAA, HKO, CWA) — Polymarket can change station at any time
  3. Station code — Polymarket may reference a different station than cities.json
  4. New cities — Polymarket has markets for cities not in our cities.json
  5. Stale cities — our cities.json lists a city Polymarket no longer covers

Run weekly via: openclaw cron ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CITIES_JSON  = PROJECT_ROOT / "config" / "cities.json"
REPORT_PATH  = PROJECT_ROOT / "state" / "city_settlement_audit.json"

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("city_settlement_audit")

GAMMA_BASE = "https://gamma-api.polymarket.com"
TAG_SLUGS  = ["daily-temperature", "weather"]

UNIT_RE     = re.compile(r"°([CF])\b", re.IGNORECASE)
WU_URL_RE   = re.compile(r"wunderground\.com[/\w\-\.]*",  re.IGNORECASE)
NOAA_RE     = re.compile(r"weather\.gov",                  re.IGNORECASE)
HKO_RE      = re.compile(r"weather\.gov\.hk",               re.IGNORECASE)
CWA_RE      = re.compile(r"cwa\.gov\.tw",                   re.IGNORECASE)
STATION_RE  = re.compile(r"\b([A-Z]{3,4})(?:\d{2,})?\b",  re.IGNORECASE)
LAT_LON_RE  = re.compile(
    r"([-+]?\d{1,3}(?:\.\d+)?)[°\s]+([-+]?\d{1,3}(?:\.\d+)?)[°\s]*[NSEW]",
    re.IGNORECASE,
)
CITY_TITLE_RE = re.compile(
    r"[Ii]n\s+([A-Z][a-zA-Z\s]{1,25}?)\s+(?:exceed|be|reach|drop|rise|surpass)",
    re.IGNORECASE,
)


def _curl_with_backoff(url: str, params: Optional[dict] = None,
                       timeout: float = 20.0, retries: int = 4) -> list | dict:
    """
    Fetch JSON from Gamma API using curl with SSL-insecure mode and retry.

    Stash VPN tunnel intercepts gamma-api DNS; stripped-HTTP_PROXY subprocess
    goes direct but hits SSL errors from the tunnel IP.  Adding -k (insecure)
    plus retry backoff recovers from transient rate-limiting.
    """
    import urllib.parse
    if params:
        url += "?" + urllib.parse.urlencode(params)
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY",
                                "http_proxy", "https_proxy")}
    for attempt in range(retries):
        cmd = ["curl", "-fsk", "--max-time", str(int(timeout)), url]
        logger.debug("curl (attempt %d/%d): %s", attempt + 1, retries, cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if result.returncode == 0:
            return json.loads(result.stdout)
        logger.warning(
            "curl rc=%d attempt %d/%d for %s — %s",
            result.returncode, attempt + 1, retries, url, result.stderr.strip(),
        )
        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(
        f"curl failed after {retries} attempts: {result.stderr.strip()}"
    )


def _fetch_events_by_tag(tag_slug: str) -> list[dict]:
    """Fetch active events for a given tag slug."""
    tag_data = _curl_with_backoff(f"{GAMMA_BASE}/tags/slug/{tag_slug}")
    tag_id = tag_data.get("id") if isinstance(tag_data, dict) else None
    if not tag_id:
        logger.warning("Tag '%s' not found: %s", tag_slug, str(tag_data)[:100])
        return []

    all_events: list[dict] = []
    offset = 0
    while True:
        batch = _curl_with_backoff(
            f"{GAMMA_BASE}/events",
            params={"tag_id": tag_id, "closed": "false", "limit": 50, "offset": offset},
        )
        if not batch:
            break
        all_events.extend(batch)
        if len(batch) < 50:
            break
        offset += 50
        time.sleep(0.3)

    return all_events


def fetch_all_weather_events() -> list[dict]:
    all_events: list[dict] = []
    for slug in TAG_SLUGS:
        try:
            events = _fetch_events_by_tag(slug)
            logger.info("Tag '%s' → %d events", slug, len(events))
            all_events.extend(events)
        except Exception as exc:
            logger.warning("Tag '%s' fetch failed: %s", slug, exc)

    seen: set[str] = set()
    unique: list[dict] = []
    for e in all_events:
        eid = e.get("id") or e.get("slug", "")
        if eid not in seen:
            seen.add(eid)
            unique.append(e)
    logger.info("Total unique weather events: %d", len(unique))
    return unique


def extract_unit(text: str) -> Optional[str]:
    """
    Extract temperature unit from market question / outcomes text.

    Polymarket temperature markets store the unit two ways:
      - explicitly in the question: "Will Tokyo exceed 35°C on 2026-04-17?"
      - as a keyword in the description: "...in degrees Celsius on 17 Apr '26"

    This function prioritises the question/outcomes field (explicit threshold values)
    over the description field (settlement language that may not reflect the market's
    own unit representation).

    The degree-symbol regex (°F/°C) catches outcome prices and threshold values.
    The keyword fallback is a last resort when no numeric threshold is present.
    """
    # 1. Look for explicit threshold values: 30°C, 85°F, etc.
    #    These appear in the question / outcomes, not in the settlement description.
    threshold_re = re.compile(r"(\d+(?:\.\d+)?)\s*[°]\s*([CF])\b", re.IGNORECASE)
    for m in threshold_re.finditer(text):
        return m.group(2).upper()

    # 2. Fallback: look for degree-symbol standalone (e.g. in outcome prices)
    m_sym = UNIT_RE.search(text)
    if m_sym:
        return m_sym.group(1).upper()

    # 3. Keyword in question or description (less reliable — only if no threshold found)
    if re.search(r"\bCelsius\b", text, re.IGNORECASE):
        return "C"
    if re.search(r"\bFahrenheit\b", text, re.IGNORECASE):
        return "F"

    return None


def extract_settlement_source(text: str) -> str:
    for pattern, label in [
        (WU_URL_RE, "WU"), (NOAA_RE, "NOAA"),
        (HKO_RE, "HKO"),   (CWA_RE, "CWA"),
    ]:
        m = pattern.search(text)
        if m:
            return m.group(0)
    snippet = text[:200].replace("\n", " ").strip()
    return f"[no_station_url] {snippet}"


def extract_station_code(text: str) -> Optional[str]:
    m = re.search(r"[Ss]tation[:\s]+([A-Z]{3,4}(?:\d+)?)", text)
    if m:
        return m.group(1).upper()
    for code in re.findall(r"\b([A-Z]{4})\b", text):
        if code not in ("WILL", "THE ", "TEMP", "HIGH", "LOW ", "EXCE",
                        "MORE", "DATE", "CITY", "HILO", "EGLC", "RKSI",
                        "USGS", "NASA"):
            return code
    return None


def extract_coords(text: str) -> Optional[tuple[float, float]]:
    m = LAT_LON_RE.search(text)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return None


def _match_city(title: str, slug: str,
                cities_by_name_lower: dict[str, dict],
                cities_by_slug: dict[str, dict]) -> Optional[dict]:
    """
    Try to match a Polymarket event to a city in cities.json.

    Strategies (in order):
      1. "in {city} on" in title  →  "Tokyo" from "in Tokyo on 2026-04-20"
      2. "{city}-hi-lo-on-{date}" slug pattern  →  "london" from slug
      3. "in-{city}-on-" in slug  →  city from "highest-temperature-in-london-on-..."

    Returns the matched city dict from cities.json (with canonical name),
    or None if no match.
    """
    slug_lower = slug.lower()
    title_words = re.sub(r"[^A-Za-z\s]", "", title).split()

    # Strategy 1: "in {city} on" in title
    m = CITY_TITLE_RE.search(title)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in cities_by_name_lower:
            return cities_by_name_lower[candidate]
        if candidate in cities_by_slug:
            return cities_by_slug[candidate]

    # Strategy 2: "{city}-hi-lo-on-" slug
    m = re.match(r"([a-z]+(?:-[a-z]+)*?)-hi-lo-on-", slug_lower)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in cities_by_name_lower:
            return cities_by_name_lower[candidate]
        if candidate in cities_by_slug:
            return cities_by_slug[candidate]

    # Strategy 3: "in-{city}-on-" in slug (for "highest-temperature-in-london-on-...")
    m = re.search(r"in-([a-z]{3,}(?:-[a-z]+)*)-on-", slug_lower)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in cities_by_name_lower:
            return cities_by_name_lower[candidate]
        if candidate in cities_by_slug:
            return cities_by_slug[candidate]

    # Strategy 4: first title word if it's a known city (≥ 4 chars)
    if title_words:
        first = title_words[0].lower()
        if first in cities_by_name_lower:
            return cities_by_name_lower[first]
        if first in cities_by_slug:
            return cities_by_slug[first]

    return None


def run_audit() -> dict:
    if not CITIES_JSON.exists():
        raise FileNotFoundError(f"cities.json not found at {CITIES_JSON}")

    with open(CITIES_JSON) as f:
        cities_data = json.load(f)

    cities_list = cities_data["cities"]
    cities_by_name_lower: dict[str, dict] = {c["name"].lower(): c for c in cities_list}
    cities_by_slug: dict[str, dict] = {}
    for c in cities_list:
        for sn in c.get("slug_names", []):
            cities_by_slug[sn.lower()] = c

    logger.info("Loaded %d cities from cities.json", len(cities_list))

    events = fetch_all_weather_events()

    # Build Polymarket city index — only for cities we track
    pm_by_city: dict[str, dict] = {}
    for event in events:
        title   = event.get("title", "")
        slug    = event.get("slug",  "")
        markets = event.get("markets", []) or []
        market  = markets[0] if markets else {}
        question = market.get("question", "") or ""
        outcomes = str(market.get("outcomes", []) or [])
        outcome_prices = str(market.get("outcomePrices", []) or [])
        desc    = market.get("description", "") or ""
        res_src = market.get("resolution_source", "") or ""

        # Unit: prefer explicit threshold values (30°C, 85°F) from question/outcomes
        # over settlement language in description.
        unit_text = f"{question} {outcomes} {outcome_prices}"
        # Description settlement language: used only for station/source extraction.
        desc_text = f"{desc} {res_src}"

        matched = _match_city(title, slug, cities_by_name_lower, cities_by_slug)
        if not matched:
            continue

        city_key    = matched["name"]           # canonical name
        city_lower  = city_key.lower()

        entry = {
            "city_name_raw":     city_key,
            "unit":              extract_unit(unit_text),
            "settlement_url":    extract_settlement_source(desc_text),
            "station":           extract_station_code(desc_text),
            "coords":            extract_coords(desc_text),
            "event_slug":        slug,
            "description":       desc[:300],
            "resolution_source": res_src[:200],
            "event_id":          event.get("id", ""),
            "_end_date":         event.get("endDate", "") or "",
        }

        existing = pm_by_city.get(city_lower)
        if not existing or entry["_end_date"] > existing.get("_end_date", ""):
            pm_by_city[city_lower] = entry

    logger.info("Unique cities matched from Polymarket: %d", len(pm_by_city))

    our_cities_lower = set(cities_by_name_lower.keys())
    pm_cities_lower  = set(k for k in pm_by_city if pm_by_city[k].get("unit"))
    stale_in_us      = our_cities_lower - pm_cities_lower
    # New cities: Polymarket has market for a city not in our config
    new_in_pm = set()
    for city_lower in pm_by_city:
        if city_lower not in our_cities_lower:
            new_in_pm.add(city_lower)

    issues: list[dict] = []

    # New cities on Polymarket
    for city_lower in sorted(new_in_pm):
        entry = pm_by_city[city_lower]
        issues.append({
            "type":              "new_city_not_in_cities_json",
            "city":              entry["city_name_raw"],
            "polymarket_unit":   entry.get("unit"),
            "polymarket_source": entry.get("settlement_url", ""),
            "polymarket_station": entry.get("station"),
            "severity":          "HIGH",
            "detail":            "Polymarket has a market for this city not in cities.json",
        })

    # Per-city checks
    for city_lower, pm_entry in sorted(pm_by_city.items()):
        if city_lower not in our_cities_lower:
            continue   # already reported above
        match = cities_by_name_lower[city_lower]
        name  = match["name"]

        # Unit
        if pm_entry.get("unit") and pm_entry["unit"] != match.get("unit"):
            issues.append({
                "type":            "unit_mismatch",
                "city":            name,
                "config_unit":     match.get("unit"),
                "polymarket_unit": pm_entry["unit"],
                "severity":        "CRITICAL",
                "detail": (
                    f"Unit mismatch: cities.json={match.get('unit')} "
                    f"Polymarket={pm_entry['unit']}. "
                    f"Desc: {pm_entry.get('description','')[:200]}"
                ),
            })

        # Settlement source
        pm_source  = pm_entry.get("settlement_url", "")
        our_source = match.get("settlement_source", "")
        if "[no_station_url]" not in pm_source and pm_source != our_source:
            issues.append({
                "type":             "settlement_source_changed",
                "city":             name,
                "config_source":   our_source,
                "polymarket_source": pm_source,
                "severity":         "HIGH",
                "detail": f"Settlement source changed. Desc: {pm_entry.get('description','')[:300]}",
            })

        # Station
        pm_station = pm_entry.get("station")
        if pm_station and pm_station not in (match.get("wu_station", ""),
                                              match.get("wu_pws", "")):
            issues.append({
                "type":               "station_changed",
                "city":               name,
                "config_station":     match.get("wu_station"),
                "polymarket_station": pm_station,
                "severity":           "HIGH",
                "detail": (
                    f"Station changed: cities.json={match.get('wu_station')} "
                    f"Polymarket={pm_station}. Source: {pm_source}"
                ),
            })

        # Coordinates
        if pm_entry.get("coords") and match.get("lat") and match.get("lon"):
            pm_lat, pm_lon = pm_entry["coords"]
            if abs(pm_lat - match["lat"]) > 0.5 or abs(pm_lon - match["lon"]) > 0.5:
                issues.append({
                    "type":               "coordinate_drift",
                    "city":               name,
                    "config_coords":     {"lat": match["lat"], "lon": match["lon"]},
                    "polymarket_coords":  {"lat": pm_lat, "lon": pm_lon},
                    "severity":           "MEDIUM",
                    "detail":             f"Coordinate drift > 0.5°. Desc: {pm_entry.get('description','')[:200]}",
                })

    report = {
        "audit_ts":              datetime.now(timezone.utc).isoformat(),
        "cities_in_config":      len(our_cities_lower),
        "cities_on_polymarket":  len(pm_by_city),
        "new_in_polymarket":     sorted(new_in_pm),
        "stale_in_our_config":   sorted(stale_in_us),
        "issues":                issues,
        "summary": {
            "critical": sum(1 for i in issues if i["severity"] == "CRITICAL"),
            "high":     sum(1 for i in issues if i["severity"] == "HIGH"),
            "medium":   sum(1 for i in issues if i["severity"] == "MEDIUM"),
            "total":    len(issues),
        },
        "polymarket_city_details": {
            k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
            for k, v in pm_by_city.items()
        },
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2, default=str)

    return report


def print_summary(report: dict) -> None:
    s = report["summary"]
    print(f"\n{'='*60}")
    print(f"  City Settlement Audit — {report['audit_ts']}")
    print(f"{'='*60}")
    print(f"  Cities in config:       {report['cities_in_config']}")
    print(f"  Cities on Polymarket:   {report['cities_on_polymarket']}")
    print()
    if report["new_in_polymarket"]:
        print(f"  NEW on Polymarket: {len(report['new_in_polymarket'])}")
        for c in report["new_in_polymarket"]:
            print(f"    + {c}")
    print()
    if report["stale_in_our_config"]:
        print(f"  STALE (no Polymarket market): {len(report['stale_in_our_config'])}")
        for c in report["stale_in_our_config"]:
            print(f"    - {c}")
    print()
    print(f"  ISSUES: {s['total']} total  |  "
          f"{s['critical']} CRITICAL  {s['high']} HIGH  {s['medium']} MEDIUM")
    for issue in report["issues"]:
        icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡"}.get(issue["severity"], "⚪")
        print(f"\n  {icon} [{issue['severity']}] {issue['type']} — {issue['city']}")
        print(f"     {issue['detail'][:150]}")
    print(f"\n  Full report: {REPORT_PATH}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    report = run_audit()
    print_summary(report)

    sys.exit(2 if report["summary"]["critical"] > 0
             else 1 if report["summary"]["high"] > 0 else 0)
