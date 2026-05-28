# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: Operator pre-MC re-audit Blocker B1 (2026-05-28). The full_transport
#   producer iterates a fixed-calendar `_SEASONS` tuple ((DJF,(12,1,2)),...) and writes
#   the row label verbatim, while the LIVE reader queries via
#   `season_from_date(target_date, lat=city.lat)` which applies `_SH_FLIP` so a southern
#   hemisphere city's calendar-Jan returns label "JJA", not "DJF". Result: rows for
#   Buenos Aires/Cape Town/Sao Paulo/Wellington are orphaned — reader queries the
#   SH-flipped label, misses the calendar-labelled row, fails open. The producer must
#   iterate hemisphere-aware so its row label matches the reader's query for every
#   (city, target_month) the row claims to cover.
"""B1 — producer row label must equal reader query label for every (city, target_month).

Relationship test: PRODUCER side _iter_seasons_for_city(city) yields (label, months)
tuples; READER side season_from_date(date, lat=city.lat) yields a label. For every
(city, month) the city onboarding covers (every month, every city), the row labelled
under the producer's iteration for the calendar-group containing `month` MUST be
exactly the label the reader will query when it sees a `target_date` in `month`.

Pre-fix: producer iterates calendar `_SEASONS` so SH cities have label mismatch.
Test imports `_iter_seasons_for_city` which does not yet exist → RED (ImportError).
"""
from __future__ import annotations

import pytest

from src.config import cities_by_name
from src.contracts.season import season_from_date, season_from_month


def test_iter_seasons_for_city_exists():
    """The producer must expose a hemisphere-aware season iterator helper.

    Pre-fix: helper does not exist → ImportError → RED.
    """
    from scripts.fit_full_transport_error_models import (  # noqa: F401, PLC0415
        _iter_seasons_for_city,
    )


def test_producer_label_equals_reader_label_for_every_city_and_month():
    """For every city and every month (1..12), the producer's row label MUST equal
    the reader's `season_from_date(...lat=city.lat)` label.

    Pre-fix: northern hemisphere matches by accident (no flip); southern hemisphere
    fails for every month — producer writes the calendar label, reader applies _SH_FLIP.
    """
    from scripts.fit_full_transport_error_models import _iter_seasons_for_city  # PLC0415

    failures: list[tuple[str, int, str, str]] = []
    for name, city in sorted(cities_by_name.items()):
        # Build month -> producer label index from the iterator.
        month_to_label: dict[int, str] = {}
        for label, months in _iter_seasons_for_city(city):
            for m in months:
                month_to_label[m] = label
        # The iterator must declare every calendar month for the city.
        if sorted(month_to_label.keys()) != list(range(1, 13)):
            failures.append((name, -1, "iterator missing months",
                             str(sorted(month_to_label.keys()))))
            continue
        for month in range(1, 13):
            date_str = f"2026-{month:02d}-15"
            reader_label = season_from_date(date_str, lat=city.lat)
            producer_label = month_to_label[month]
            if reader_label != producer_label:
                failures.append((name, month, reader_label, producer_label))

    assert not failures, (
        "Producer↔reader label mismatch for (city, month) pairs (showing first 10): "
        f"{failures[:10]}"
    )


@pytest.mark.parametrize(
    "city_name, month, expected_label_via_lat",
    [
        # Northern hemisphere: producer & reader agree on calendar labels.
        ("Atlanta", 1, "DJF"),
        ("Atlanta", 4, "MAM"),
        ("Atlanta", 7, "JJA"),
        ("Atlanta", 10, "SON"),
        # Southern hemisphere: reader returns the SH-flipped label, producer must too.
        ("Buenos Aires", 1, "JJA"),   # calendar Jan = SH "cold season" = JJA
        ("Cape Town", 4, "SON"),
        ("Sao Paulo", 7, "DJF"),
        ("Wellington", 10, "MAM"),
    ],
)
def test_specific_sh_cities_get_flipped_labels(
    city_name: str, month: int, expected_label_via_lat: str
):
    """Sanity: cities whose VERIFIED rows are currently orphaned must receive the
    flipped label from the producer iterator, exactly as the reader queries it.

    Pre-fix RED: _iter_seasons_for_city does not exist; even if it did, the calendar
    iteration would emit "DJF" for BA's calendar Jan, not "JJA".
    """
    from scripts.fit_full_transport_error_models import _iter_seasons_for_city  # PLC0415

    city = cities_by_name[city_name]
    # Confirm the reader fixture: derived from city.lat alone.
    assert season_from_date(f"2026-{month:02d}-15", lat=city.lat) == expected_label_via_lat
    assert season_from_month(month, lat=city.lat) == expected_label_via_lat

    month_to_label = {
        m: label
        for label, months in _iter_seasons_for_city(city)
        for m in months
    }
    assert month_to_label[month] == expected_label_via_lat, (
        f"{city_name} month={month}: producer iterates {month_to_label[month]!r} "
        f"but reader queries {expected_label_via_lat!r}"
    )
