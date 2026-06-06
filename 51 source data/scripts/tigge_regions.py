#!/usr/bin/env python3
"""Shared TIGGE region grouping for faster batched downloads."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TiggeRegion:
    name: str
    north: float
    west: float
    south: float
    east: float

    @property
    def area(self) -> str:
        return f"{self.north}/{self.west}/{self.south}/{self.east}"


REGIONS: tuple[TiggeRegion, ...] = (
    TiggeRegion("americas", north=60.0, west=-130.0, south=-60.0, east=-30.0),
    TiggeRegion("europe_africa", north=65.0, west=-30.0, south=-40.0, east=60.0),
    TiggeRegion("asia", north=60.0, west=60.0, south=-10.0, east=150.0),
    TiggeRegion("oceania", north=10.0, west=110.0, south=-50.0, east=180.0),
)


def region_for_city(*, lat: float, lon: float) -> TiggeRegion:
    for region in REGIONS:
        if region.south <= lat <= region.north and region.west <= lon <= region.east:
            return region
    raise ValueError(
        "CRITICAL: City coordinates do not fall into any defined TIGGE region box: "
        f"lat={lat}, lon={lon}. Expand REGIONS in tigge_regions.py."
    )


__all__ = ["TiggeRegion", "REGIONS", "region_for_city"]
