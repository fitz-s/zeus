# Created: 2026-09-12
# Authority basis: T-group vs body-group law verified on 434 settled city-days
#   2026-08-23..09-11 (11 US F-settled NOAA cities): T-group law matches
#   chain-winning bins 428/434, whole-degree body law only 336/434. Unifies
#   three independently-drifted METAR temperature parsers
#   (ogimet_hourly_client, daily_obs_append, backfill_ogimet_metar) that all
#   read the whole-degree body group and ignored the tenths-precision T-group.
"""Shared METAR temperature parser: T-group precision, body-group fallback.

A METAR body carries whole-degree Celsius in the temp/dewpoint group
(``28/17``). US ASOS stations also emit a remarks T-group (``T02830167``):
tenths-Celsius, first digit of each half is a sign bit (1 = negative), the
remaining three digits are the magnitude in tenths. NOAA settlement pages
resolve bin ties from the tenths value, so the T-group must be preferred
whenever present; the body group is only a fallback for stations/reports
that omit remarks.
"""
from __future__ import annotations

import re

#: T-group (temperature to tenths C) in the raw METAR remarks, e.g.
#: "T02110150". The LAST occurrence in the report is authoritative.
_T_GROUP_RE = re.compile(r"\bT\d{8}\b")

#: Body temp/dewpoint group, e.g. "28/17", "M05/M08". The dewpoint half may
#: be reported missing as "//" (e.g. "22///"); the temperature half is still
#: valid in that case. The dewpoint value itself is never used.
_METAR_TEMP_RE = re.compile(r"(?:^|\s)(M?\d{2})/(?:M?\d{2}|//)(?:\s|$)")


def metar_t_group_temperature_c(raw: str) -> float | None:
    """Return the precise tenths-Celsius METAR T-group value, if present."""

    groups = _T_GROUP_RE.findall(raw)
    if not groups:
        return None
    token = groups[-1]
    sign = -1.0 if token[1] == "1" else 1.0
    return sign * int(token[2:5]) / 10.0


def metar_temperature_c(raw: str) -> float | None:
    """Extract METAR temperature in Celsius: T-group first, body fallback.

    Returns ``None`` when the report carries neither a valid T-group nor a
    valid body temp group.
    """

    precise = metar_t_group_temperature_c(raw)
    if precise is not None:
        return precise
    match = _METAR_TEMP_RE.search(raw)
    if match is None:
        return None
    token = match.group(1)
    return float(-int(token[1:]) if token.startswith("M") else int(token))
