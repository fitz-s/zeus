# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — Polymarket has been silently
#   migrating settlement stations every few days (Tel Aviv 2026-04-15, Taipei
#   2026-04-15, Paris 2026-04-18/19 LFPG→LFPB). Pattern is recurring.
#   Antibody invariant F: a daemon job compares Polymarket gamma resolution
#   URLs against config/cities.json::wu_station and writes alerts when they
#   diverge. Operator must consciously approve every migration.
"""Station-migration drift probe.

For every city in the Zeus universe, fetch the most-recent active Polymarket
market description, extract the resolution-source URL, parse out the station
code (the trailing ICAO/IATA code in the WU history URL), and compare against
``config/cities.json::wu_station``.

When a mismatch is detected, the probe writes an alert entry to
``state/station_migration_alerts.json`` and bumps the per-city primary-source
``degraded_since`` field in ``state/source_health.json`` so the operator
observes the drift before it pollutes downstream pipelines.

This module is INTENTIONALLY non-mutating — ``config/cities.json`` is never
auto-rewritten. The operator must explicitly approve each migration via a
manual config edit (mirroring the Paris LFPB/LFPG handling on 2026-05-01).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Pattern matches the trailing ICAO/IATA code in WU URLs, e.g.
# https://www.wunderground.com/history/daily/uk/london/EGLL → "EGLL"
_WU_STATION_RE = re.compile(r"/([A-Za-z0-9]{3,5})/?$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def parse_station_from_url(url: Optional[str]) -> Optional[str]:
    """Extract the trailing station code from a WU resolution URL.

    Returns the uppercase station code, or None if the URL is missing or the
    pattern does not match. Examples::

        "https://www.wunderground.com/history/daily/uk/london/EGLL" → "EGLL"
        "https://www.wunderground.com/history/daily/fr/paris/LFPB"  → "LFPB"
        None / empty / unrelated URL                                 → None
    """
    if not url:
        return None
    m = _WU_STATION_RE.search(str(url).strip())
    if not m:
        return None
    return m.group(1).upper()


def compare_cities_against_gamma(
    *,
    cities: list[dict],
    gamma_lookup: dict[str, str],
) -> list[dict]:
    """Diff configured stations against gamma-reported stations.

    Parameters
    ----------
    cities :
        List of dicts as found in ``config/cities.json::cities``. Required
        keys: ``name``, ``wu_station``, ``settlement_source``.
    gamma_lookup :
        ``{city_name: resolution_source_url}`` from the Polymarket gamma probe.
        A missing entry is treated as "no current market" and skipped.

    Returns
    -------
    List of alert dicts (one per mismatch). Empty when fully aligned.
    """
    alerts: list[dict] = []
    for city in cities:
        name = str(city.get("name") or city.get("city") or "")
        configured = str(city.get("wu_station") or "").upper()
        if not name or not configured:
            continue
        gamma_url = gamma_lookup.get(name)
        if not gamma_url:
            continue
        gamma_station = parse_station_from_url(gamma_url)
        if not gamma_station:
            # The gamma URL is structurally unparseable — surface as a drift
            # so the operator looks at it (could be a structural format change).
            alerts.append({
                "city": name,
                "configured_station": configured,
                "gamma_station": None,
                "gamma_url": gamma_url,
                "severity": "WARN",
                "reason": "gamma_url_not_parseable",
                "detected_at": _now_iso(),
            })
            continue
        if gamma_station != configured:
            alerts.append({
                "city": name,
                "configured_station": configured,
                "gamma_station": gamma_station,
                "gamma_url": gamma_url,
                "severity": "ALERT",
                "reason": "station_mismatch",
                "detected_at": _now_iso(),
            })
    return alerts


def _bump_source_health_for_city(
    source_health_path: Path,
    *,
    city: str,
    primary_source: str = "wu_pws",
) -> None:
    """Stamp ``degraded_since`` on the city's primary-source health entry.

    Best-effort: silently no-ops if the source-health file is unreadable
    (it may be mid-write by the source_health_probe job) or the requested
    source key is absent.
    """
    if not source_health_path.exists():
        return
    try:
        data = json.loads(source_health_path.read_text())
    except Exception:
        return
    sources = data.get("sources") or {}
    entry = sources.get(primary_source)
    if not isinstance(entry, dict):
        return
    cities_dict = entry.setdefault("station_migration_cities", {})
    cities_dict[city] = _now_iso()
    if entry.get("degraded_since") is None:
        entry["degraded_since"] = _now_iso()
    sources[primary_source] = entry
    data["sources"] = sources
    try:
        _atomic_write_json(source_health_path, data)
    except Exception as exc:
        logger.warning("station_migration_probe could not bump source_health: %s", exc)


def run_probe(
    *,
    cities_json_path: Optional[Path] = None,
    state_dir: Optional[Path] = None,
    gamma_fetcher=None,
) -> dict:
    """Top-level probe entry. Returns a summary dict.

    Parameters
    ----------
    cities_json_path :
        Override path to ``config/cities.json``. Default: repo's config.
    state_dir :
        Override Zeus ``state/`` dir. Default: ``state_path("").parent``.
    gamma_fetcher :
        Callable ``(city_names: list[str]) -> dict[str, str]`` returning a
        ``{city: resolution_source_url}`` map. Test seam — tests inject a
        synthetic mismatch. The production caller passes the real gamma
        fetcher (see ``_default_gamma_fetcher`` for plumbing notes).
    """
    from src.config import PROJECT_ROOT, state_path

    if cities_json_path is None:
        cities_json_path = PROJECT_ROOT / "config" / "cities.json"
    if state_dir is None:
        state_dir = Path(str(state_path(""))).parent

    cities_data = json.loads(cities_json_path.read_text())
    cities = cities_data.get("cities") if isinstance(cities_data, dict) else cities_data
    if not isinstance(cities, list):
        raise ValueError(f"Unexpected cities.json shape: {type(cities)!r}")

    if gamma_fetcher is None:
        gamma_fetcher = _default_gamma_fetcher

    city_names = [str(c.get("name") or c.get("city") or "") for c in cities]
    city_names = [n for n in city_names if n]
    try:
        gamma_lookup = gamma_fetcher(city_names)
    except Exception as exc:
        logger.warning("station_migration_probe: gamma fetch failed: %s", exc)
        return {
            "status": "gamma_fetch_failed",
            "error": str(exc),
            "alerts_count": 0,
            "alerts": [],
        }

    alerts = compare_cities_against_gamma(cities=cities, gamma_lookup=gamma_lookup)

    alerts_path = state_dir / "station_migration_alerts.json"
    payload = {
        "written_at": _now_iso(),
        "alerts_count": len(alerts),
        "alerts": alerts,
    }
    _atomic_write_json(alerts_path, payload)

    if alerts:
        sh_path = state_dir / "source_health.json"
        for alert in alerts:
            _bump_source_health_for_city(sh_path, city=alert["city"])

    return {
        "status": "ok",
        "alerts_count": len(alerts),
        "alerts": alerts[:10],  # truncate for log readability
        "alerts_path": str(alerts_path),
    }


def _default_gamma_fetcher(city_names: list[str]) -> dict[str, str]:
    """Default gamma fetcher — pulls resolution URLs from the live gamma API.

    Uses the existing market-discovery client lazily to avoid pulling httpx
    into the import path of station_migration_probe (which is also imported
    by tests under in-memory mocks).

    Implementation detail: we look up the most recent active market per city
    and read its ``resolutionSource`` field. Cities with no current open
    market simply absent from the returned dict.
    """
    try:
        from src.data.polymarket_gamma_client import (  # type: ignore[import]
            fetch_open_markets_resolution_sources,
        )
        return fetch_open_markets_resolution_sources(city_names)
    except Exception as exc:
        logger.warning(
            "station_migration_probe: real gamma fetcher unavailable (%s); "
            "returning empty map. Test fixtures should pass gamma_fetcher explicitly.",
            exc,
        )
        return {}


__all__ = [
    "compare_cities_against_gamma",
    "parse_station_from_url",
    "run_probe",
]
