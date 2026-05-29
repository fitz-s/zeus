# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (ForecastObject contract); CRITIC_SYNTHESIS_2026-05-29
#   §2a (product segregation mx2t3 vs mx2t6) + Cons-SEV-1.C (members_unit) + writer/reader
#   seam enforcement. Reuses validate_members_unit (ensemble_snapshot_provenance) and the
#   ForecastTarget identity (forecast_target).
"""Typed forecast random variable, constructed fail-closed from a snapshot row.

A ``ForecastObject`` binds the forecast's identity (product, cycle, lead, window,
members) to the ``ForecastTarget`` it claims as its settlement truth. It can only
be constructed when every random-variable-defining field is present and valid;
``from_snapshot_row`` RAISES otherwise, so the writer/reader seam never forwards a
half-defined RV into calibration or serving.

Design notes:
- ``product`` is the GRIB extrema token (mx2t3 / mn2t3 / mx2t6 / mn2t6). The 3h
  (t3) and 6h (t6) windows are DIFFERENT random variables (asymmetry SEV-1-B) — the
  token keeps them separable for product-segregated keying.
- ``lead_hours`` is retained RAW. The lead-bucket boundary choice is a P3 statistical
  decision (it must respect the short-lead sign-flip); the contract does not lock it.
- Lead is a property of the forecast, NOT of the target: the same settlement is the
  payout truth regardless of which lead forecast is compared against it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from src.contracts.ensemble_snapshot_provenance import validate_members_unit
from src.contracts.forecast_target import ForecastTarget

_PRODUCT_TOKEN_RE = re.compile(r"(m[xn]2t[36])")


class ForecastObjectIncompleteError(ValueError):
    """Raised when a snapshot row is missing a field required to define the
    forecast random variable. The row is refused, not silently half-served.
    """


def _require(row: dict, key: str, *, human: str | None = None):
    value = row.get(key)
    if value is None or (isinstance(value, str) and value == ""):
        label = human or key
        raise ForecastObjectIncompleteError(
            f"ForecastObject refused: required field {label!r} (row key {key!r}) "
            f"is missing or empty. A forecast random variable cannot be defined "
            f"without it."
        )
    return value


def _product_from_data_version(data_version: str) -> str:
    m = _PRODUCT_TOKEN_RE.search(data_version)
    if not m:
        raise ForecastObjectIncompleteError(
            f"ForecastObject refused: data_version={data_version!r} carries no "
            f"recognizable product token (expected one of mx2t3/mn2t3/mx2t6/mn2t6)."
        )
    return m.group(1)


def _cycle_from_iso(ts: str) -> str:
    """'...T12:00:00+00:00' -> '12z'. Tolerates a trailing 'Z'."""
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return f"{parsed.hour:02d}z"


@dataclass(frozen=True)
class ForecastObject:
    """A forecast random variable plus the settlement target it claims."""

    product: str
    cycle: str
    lead_hours: float
    issue_time: str
    forecast_window_start_utc: str
    forecast_window_end_utc: str
    members: list[float]
    members_unit: str
    target: ForecastTarget

    @classmethod
    def from_snapshot_row(cls, row: dict) -> "ForecastObject":
        """Build a ForecastObject from an ``ensemble_snapshots`` row dict.

        RAISES ForecastObjectIncompleteError on any missing RV-defining field, and
        MembersUnitInvalidError (via validate_members_unit) on a bad/missing unit.
        """
        data_version = _require(row, "data_version")
        product = _product_from_data_version(str(data_version))

        members_unit = row.get("members_unit")
        validate_members_unit(members_unit, context="ForecastObject.from_snapshot_row")

        raw_members = _require(row, "members_json", human="members")
        members = (
            json.loads(raw_members) if isinstance(raw_members, str) else list(raw_members)
        )

        issue_time = str(_require(row, "issue_time"))
        cycle_source = row.get("source_cycle_time") or issue_time
        cycle = _cycle_from_iso(str(cycle_source))

        target = ForecastTarget(
            city=str(_require(row, "city")),
            metric=str(_require(row, "temperature_metric", human="metric")),
            target_local_date=str(_require(row, "target_date", human="target_local_date")),
            settlement_station=str(_require(row, "settlement_station_id", human="settlement_station")),
            settlement_unit=str(_require(row, "settlement_unit")),
            settlement_authority=str(_require(row, "settlement_source_type", human="settlement_authority")),
        )

        return cls(
            product=product,
            cycle=cycle,
            lead_hours=float(_require(row, "lead_hours")),
            issue_time=issue_time,
            forecast_window_start_utc=str(_require(row, "forecast_window_start_utc", human="forecast_window_start")),
            forecast_window_end_utc=str(_require(row, "forecast_window_end_utc", human="forecast_window_end")),
            members=[float(m) for m in members],
            members_unit=str(members_unit),
            target=target,
        )
