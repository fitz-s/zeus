# Created: 2026-06-17
# Last reused or audited: 2026-06-17
# Authority basis: operator spec zeus_source_access_validation_v3.xlsx, CityBestSources sheet
#   (all 54 cities marked FAIL_INPUT_TRUNCATED; coordinate_action =
#   "RESTORE_FROM_CONFIG_CITIES_JSON_OR_SOURCE_OF_TRUTH_AS_TEXT_DECIMAL; DO_NOT_ROUND").
#   GridCorrectionMath rule 1 (CoordinatePrecisionGuard: <4 decimal places FAILS;
#   coords stored/compared as TEXT-DECIMAL, never float-rounded).
#   Operator message: native-grid precision engine, build-only (no live-fusion wiring).
"""CoordinatePrecisionGuard — the text-decimal coordinate precision gate (v3 rule 1).

THE PRECISION GATE. A station-identity coordinate is the load-bearing input to the
whole grid-interpolation chain (rule 2/3): if the station (phi, lambda) is wrong by
the float-rounding of a 2-3 decimal coordinate, the 4 surrounding native-grid points
chosen and the haversine d_eff are wrong, so the entire representativeness correction
is wrong. The operator audit marks every city FAIL_INPUT_TRUNCATED and the only legal
remedy is to RESTORE the precise text-decimal coordinate from a source of truth and
NEVER round.

WHY TEXT-DECIMAL (not float): a coordinate written ``39.12`` carries 2 decimal places
of real precision; ``float("39.12")`` is ``39.12000000000000...`` which has no honest
notion of "2 decimals". The precision a coordinate ACTUALLY has is the number of
decimal digits in its WRITTEN form. So the guard counts decimals in the STRING and
stores/compares coordinates as text. A coordinate that has been through a float round-
trip (e.g. printed at full float width) is indistinguishable from a precise one and
would silently PASS a float-based check — that is exactly the truncation defect this
guard exists to catch.

WHAT THIS MODULE OWNS:
  - ``CoordinatePrecisionGuard(coord_text) -> CoordPrecisionVerdict`` : PASS iff the
    written form has >= MIN_DECIMALS (4) decimal places. Text-decimal aware: it counts
    the decimal digits in the string, it does NOT parse to float and re-measure.
  - ``guard_pair(lat_text, lon_text)`` : a (lat, lon) pair PASSES iff BOTH pass.
  - ``load_city_coordinates(cities_json_path)`` : reads config/cities.json (the source
    of truth) as TEXT — preserving the written precision — and returns, per city, the
    text coordinates + a per-city verdict. Cities still < 4-decimal are flagged
    ``REQUIRES_PRECISE_RESTORE`` with the operator action string. This module NEVER
    fabricates a more-precise coordinate; restoration is an operator/source-of-truth
    step, the loader only DIAGNOSES.

PURITY: no network, no DB writes. Reading the local cities.json file is the only I/O
and only in the explicit loader.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

# Operator rule 1: a station-identity coordinate with < 4 decimal places FAILS.
MIN_DECIMALS = 4

# The exact operator remediation action (verbatim from the CityBestSources sheet).
RESTORE_ACTION = (
    "RESTORE_FROM_CONFIG_CITIES_JSON_OR_SOURCE_OF_TRUTH_AS_TEXT_DECIMAL; DO_NOT_ROUND"
)

# A coordinate token as it may appear in a JSON/text source: optional sign, integer
# part, optional fractional part. We forbid exponent form (e.g. 1e-3) because it hides
# the true written decimal precision — a coordinate must be a plain decimal literal.
_COORD_RE = re.compile(r"^[+-]?\d+(?:\.(\d+))?$")


def count_decimals(coord_text: str) -> int:
    """Number of decimal digits in the WRITTEN form of ``coord_text``.

    Counts the characters after the '.', NOT the precision of a parsed float. This is
    the whole point of the text-decimal contract: ``count_decimals("39.1200")`` == 4
    and ``count_decimals("39.12")`` == 2, even though ``float`` would equate the two
    only after a lossy round-trip.

    Raises ``ValueError`` on a non-decimal-literal (empty, exponent form, junk) so a
    malformed coordinate can never silently be treated as zero-decimal and "fail" for
    the wrong reason — the caller must hand a real decimal literal.
    """
    if coord_text is None:
        raise ValueError("coordinate text is None")
    s = coord_text.strip()
    m = _COORD_RE.match(s)
    if m is None:
        raise ValueError(
            f"coordinate {coord_text!r} is not a plain decimal literal "
            f"(exponent form / junk is rejected; text-decimal required)"
        )
    frac = m.group(1)
    return len(frac) if frac is not None else 0


@dataclass(frozen=True)
class CoordPrecisionVerdict:
    """The PASS/FAIL precision verdict for one coordinate (or pair).

    ``coord_text`` is kept verbatim (text-decimal) so a downstream consumer never has
    to re-derive precision from a float. ``decimals`` is the counted written precision.
    ``status`` is PASS only when ``decimals >= MIN_DECIMALS``. On FAIL, ``action`` holds
    the operator remediation string and ``reason`` names the defect.
    """

    coord_text: str
    decimals: int
    status: Literal["PASS", "FAIL"]
    reason: str = ""
    action: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def CoordinatePrecisionGuard(coord_text: str) -> CoordPrecisionVerdict:  # noqa: N802
    """Guard one coordinate. PASS iff its written form has >= 4 decimal places.

    Named with the operator's PascalCase (rule 1 ``CoordinatePrecisionGuard``) even
    though it is a function: it is the single named gate the spec refers to.
    """
    decimals = count_decimals(coord_text)
    if decimals >= MIN_DECIMALS:
        return CoordPrecisionVerdict(
            coord_text=coord_text.strip(), decimals=decimals, status="PASS"
        )
    return CoordPrecisionVerdict(
        coord_text=coord_text.strip(),
        decimals=decimals,
        status="FAIL",
        reason="FAIL_INPUT_TRUNCATED",
        action=RESTORE_ACTION,
    )


@dataclass(frozen=True)
class CityCoordRecord:
    """One city's text-decimal coordinates + the pair verdict from the loader.

    ``lat_text`` / ``lon_text`` are the coordinates exactly as written in the source
    of truth (config/cities.json). ``status`` is PASS only when BOTH coordinates pass.
    ``restore_status`` is ``REQUIRES_PRECISE_RESTORE`` when the pair fails — that is the
    flag a later restoration step keys on; the loader never invents a precise value.
    """

    name: str
    lat_text: str
    lon_text: str
    lat_verdict: CoordPrecisionVerdict
    lon_verdict: CoordPrecisionVerdict
    status: Literal["PASS", "FAIL"]
    restore_status: Literal["OK", "REQUIRES_PRECISE_RESTORE"]
    action: str = ""


def guard_pair(lat_text: str, lon_text: str) -> tuple[
    CoordPrecisionVerdict, CoordPrecisionVerdict, bool
]:
    """Guard a (lat, lon) pair. Returns (lat_verdict, lon_verdict, both_passed)."""
    lat_v = CoordinatePrecisionGuard(lat_text)
    lon_v = CoordinatePrecisionGuard(lon_text)
    return lat_v, lon_v, (lat_v.passed and lon_v.passed)


# Match a city object's "name", "lat", "lon" tokens in the RAW json text so the WRITTEN
# precision of the coordinate survives (json.load -> float would erase it).
_NAME_RE = re.compile(r'"name"\s*:\s*"((?:[^"\\]|\\.)*)"')
_LAT_RE = re.compile(r'"lat"\s*:\s*([+-]?\d+(?:\.\d+)?)')
_LON_RE = re.compile(r'"lon"\s*:\s*([+-]?\d+(?:\.\d+)?)')


def load_city_coordinates(cities_json_path: str) -> list[CityCoordRecord]:
    """Load per-city TEXT-DECIMAL coordinates from cities.json and diagnose precision.

    config/cities.json is the operator-designated source of truth. We read it as TEXT
    and extract each city's lat/lon by their WRITTEN form (a json.load would coerce to
    float and destroy the decimal precision this guard measures). Each city gets a pair
    verdict; cities whose pair fails are flagged ``REQUIRES_PRECISE_RESTORE`` carrying
    the operator action string.

    This loader NEVER fabricates a more-precise coordinate. Restoration is an operator /
    source-of-truth step; here we only surface which cities still need it.
    """
    raw = open(cities_json_path, encoding="utf-8").read()
    # Anchor on the names array order so each block is the slice between consecutive names.
    parsed = json.loads(raw)
    names = [c["name"] for c in parsed["cities"]]

    # Locate each name occurrence (the city object header) to bound per-city blocks.
    positions: list[tuple[int, str]] = []
    for n in names:
        m = re.search(r'"name"\s*:\s*"' + re.escape(n) + r'"', raw)
        if m is None:
            raise ValueError(f"city {n!r} present in parse but not findable in raw text")
        positions.append((m.start(), n))
    positions.sort()
    positions.append((len(raw), "__END__"))

    records: list[CityCoordRecord] = []
    for i in range(len(positions) - 1):
        start, name = positions[i]
        end = positions[i + 1][0]
        block = raw[start:end]
        lat_m = _LAT_RE.search(block)
        lon_m = _LON_RE.search(block)
        if lat_m is None or lon_m is None:
            # A city without a textual lat/lon literal cannot be precision-checked; flag
            # it for restore rather than fabricating.
            records.append(
                CityCoordRecord(
                    name=name,
                    lat_text=(lat_m.group(1) if lat_m else ""),
                    lon_text=(lon_m.group(1) if lon_m else ""),
                    lat_verdict=CoordPrecisionVerdict("", 0, "FAIL", "MISSING_COORD", RESTORE_ACTION),
                    lon_verdict=CoordPrecisionVerdict("", 0, "FAIL", "MISSING_COORD", RESTORE_ACTION),
                    status="FAIL",
                    restore_status="REQUIRES_PRECISE_RESTORE",
                    action=RESTORE_ACTION,
                )
            )
            continue
        lat_text, lon_text = lat_m.group(1), lon_m.group(1)
        lat_v, lon_v, both = guard_pair(lat_text, lon_text)
        records.append(
            CityCoordRecord(
                name=name,
                lat_text=lat_text,
                lon_text=lon_text,
                lat_verdict=lat_v,
                lon_verdict=lon_v,
                status="PASS" if both else "FAIL",
                restore_status="OK" if both else "REQUIRES_PRECISE_RESTORE",
                action="" if both else RESTORE_ACTION,
            )
        )
    return records


def cities_requiring_restore(records: list[CityCoordRecord]) -> list[CityCoordRecord]:
    """The subset of cities still < 4-decimal — the precise-restore worklist."""
    return [r for r in records if r.restore_status == "REQUIRES_PRECISE_RESTORE"]
