# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: critic SEV-1 (Qingdao silent DDD_CITY_UNCONFIGURED lockout);
#                  docs/reference/zeus_oracle_density_discount_reference.md (DDD v2);
#                  src/engine/ddd_wiring.py:304-333 (fail-CLOSED on unconfigured city)
"""Antibody: every tradeable city MUST be configured in BOTH DDD artifacts.

Category made impossible: a city present in config/cities.json with training
data silently falling through DDD's fail-CLOSED gate because it was never added
to v2_city_floors.json::per_city and v2_nstar.json::per_city_metric. That gate
(ddd_wiring.evaluate_ddd_for_decision -> DDDFailClosed(DDD_CITY_UNCONFIGURED))
blocks the city from trading entirely with no operator-visible signal.

Qingdao was the instance (23 oracle obs, 22 training dates, healthy coverage,
yet absent from both configs). This test asserts the relationship across the
three config files so the NEXT onboarded city cannot regress the same way.

A city is "tradeable" for DDD purposes unless it is explicitly tagged
status='NO_TRAIN_DATA' in v2_city_floors.json (Hong Kong / Istanbul / Moscow /
Tel Aviv — documented intentional exclusions). Those four are allowed to be
absent from a usable floor, but they MUST still carry the explicit status tag
(not be silently missing) and MUST still have n_star entries.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CITIES = _REPO_ROOT / "config" / "cities.json"
_FLOORS = _REPO_ROOT / "src" / "oracle" / "ddd_artifacts" / "v2_city_floors.json"
_NSTAR = _REPO_ROOT / "src" / "oracle" / "ddd_artifacts" / "v2_nstar.json"

# Cities explicitly excluded with documented status; allowed to lack a usable
# floor but must still carry the status tag (verified separately below).
_INTENTIONAL_NO_TRAIN = {"Hong Kong", "Istanbul", "Moscow", "Tel Aviv"}


def _load(p: Path) -> dict:
    with p.open() as f:
        return json.load(f)


def _all_city_names() -> list[str]:
    return [c["name"] for c in _load(_CITIES)["cities"]]


def test_every_city_has_floors_entry():
    """Every city in cities.json must appear in v2_city_floors.per_city."""
    per_city = _load(_FLOORS)["per_city"]
    missing = [c for c in _all_city_names() if c not in per_city]
    assert not missing, (
        f"Cities in config/cities.json absent from v2_city_floors.per_city "
        f"(would hit DDD_CITY_UNCONFIGURED fail-CLOSED and silently not trade): {missing}"
    )


def test_every_tradeable_city_has_usable_floor_or_documented_status():
    """A floors entry must be either a usable floor or an explicit status tag."""
    per_city = _load(_FLOORS)["per_city"]
    bad = []
    for city in _all_city_names():
        entry = per_city.get(city)
        if entry is None:
            continue  # covered by test_every_city_has_floors_entry
        if isinstance(entry, dict) and "status" in entry:
            # Status-tagged exclusion is acceptable only if intentional.
            if city not in _INTENTIONAL_NO_TRAIN:
                bad.append((city, f"unexpected status={entry['status']!r}"))
            continue
        if not isinstance(entry, dict) or entry.get("final_floor") is None:
            bad.append((city, "no final_floor and no status tag"))
    assert not bad, f"Floors entries that are neither usable nor documented-excluded: {bad}"


def test_every_city_has_nstar_entry_both_tracks():
    """Every city must have a v2_nstar.per_city_metric entry for high AND low.

    NO_TRAIN_DATA cities are NOT exempt: ddd_wiring fail-closes on the floor
    status BEFORE reading n_star, but the n_star artifact is the source of the
    small-sample amplifier and must stay complete so a future floor-status flip
    (city becomes tradeable) cannot leave n_star silently absent.
    """
    nstar = _load(_NSTAR)["per_city_metric"]
    missing = []
    for city in _all_city_names():
        for track in ("high", "low"):
            if f"{city}_{track}" not in nstar:
                missing.append(f"{city}_{track}")
    assert not missing, (
        f"(city, track) pairs absent from v2_nstar.per_city_metric: {missing}"
    )


def test_no_nstar_null_without_explicit_status():
    """A null N_star is only acceptable with an explicit non-OK status tag.

    Guards SEV-2: a null N_star makes data_density_discount.get_n_star return
    None, which forces small_sample=True unconditionally (the permanent 1.25x
    amplifier). That is only defensible when explicitly chosen + documented.
    """
    nstar = _load(_NSTAR)["per_city_metric"]
    bad = []
    for key, entry in nstar.items():
        if entry.get("N_star") is None:
            status = entry.get("status")
            if status in (None, "OK"):
                bad.append((key, f"null N_star with status={status!r}"))
    assert not bad, (
        f"Null N_star without an explicit non-OK status (permanent 1.25x "
        f"amplifier with no documented intent): {bad}"
    )
