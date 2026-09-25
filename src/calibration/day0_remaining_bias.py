# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24
#   (27,513 settled carrier rows: HIGH local 00-12 settled - mean member remaining max
#   = +0.27..+0.50 degC at a served width that already matches the residual sd).
#   Fitted by scripts/fit_day0_remaining_center_bias.py; applied by
#   src/data/day0_hourly_vectors.build_day0_remaining_probability_carrier.
"""Settlement-graded center bias of the Day0 remaining-day carrier.

WHAT IT CORRECTS. The remaining-hourly member extremes the shared Day0 carrier
integrates are cold in some (metric, local-hour band) cells while its width is
right. The fitted value ``b`` (degC) moves those member centers; nothing else.

WHERE IT APPLIES. ``b`` is added to the remaining-hourly member centers before the
observed-boundary max/min and the settlement integration, identically in the point
q and the confidence draws (the carrier builder owns both). The observed extreme and
the separately typed final-daily provider centers are never shifted. The persisted
``day0_remaining_carrier_future_extremes_c`` stays the UNSHIFTED member vector: it is
the residual basis the fitter reads, and a fit on already-shifted centers would
measure its own correction and unwind it.

QUALIFIED FALLBACK, NOT FAIL-OPEN. A missing, stale, malformed or future-dated
artifact serves shift 0 with status ``artifact_unavailable``; a cell that is absent
or was not activated by the walk-forward gate serves shift 0 with status
``inactive_cell``. Shift 0 is acceptable only because the unshifted recipe is the one
already live. Every carrier stamps the status, so an absent artifact is visible in
provenance, never silent. This module never raises into the decision path and never
reads a database.

WALK-FORWARD. The fitter trains on settled days strictly before ``fit_date`` whose
labels were known before that date began (UTC). An artifact whose ``fit_date`` lies
after the decision date could have seen the decision day's outcome and is refused.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_LOG = logging.getLogger("zeus.day0_remaining_bias")

ARTIFACT_FILENAME = "day0_remaining_center_bias.json"
SCHEMA_VERSION = 1
# Local-hour band width of one cell: ``metric|band`` with band = floor(hour/2)*2.
BAND_HOURS = 2
# Sanity rail on the raw estimate, not a tuning knob: the study's largest cell is
# +0.50 degC. A value past this is a unit flip or a corrupted settlement row, and the
# whole artifact is refused rather than served.
MAX_ABS_SHIFT_C = 2.0
# The refit is daily. A week without a successful refit means the producer is broken;
# the shift is then no longer current evidence and the unshifted recipe serves.
MAX_ARTIFACT_AGE_DAYS = 7

APPLIED = "applied"
INACTIVE_CELL = "inactive_cell"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"


def cell_key(metric: str, local_hour: float) -> str:
    """``metric|band`` for a local clock hour in [0, 24)."""

    return f"{metric}|{int(local_hour) // BAND_HOURS * BAND_HOURS}"


@dataclass(frozen=True, slots=True)
class Day0RemainingBias:
    """The shift one carrier build applies, with the reason it is (or is not) nonzero."""

    shift_c: float
    status: str
    artifact: str | None

    def provenance(self) -> dict[str, object]:
        return {
            "day0_remaining_center_bias_c": self.shift_c,
            "day0_remaining_bias_status": self.status,
            "day0_remaining_bias_artifact": self.artifact,
        }


_UNAVAILABLE = Day0RemainingBias(0.0, ARTIFACT_UNAVAILABLE, None)


def _shift_value(raw: object) -> float:
    value = float(raw)
    if not math.isfinite(value) or abs(value) > MAX_ABS_SHIFT_C:
        raise ValueError(f"day0 remaining bias out of range: {raw!r}")
    return value


class RemainingBiasTable:
    """Validated lookup over the fitted artifact. Pure; no I/O."""

    __slots__ = ("_cells", "_fit_date", "_identity")

    def __init__(self, artifact: Mapping[str, Any], *, identity: str) -> None:
        if int(artifact.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError("day0 remaining bias artifact schema_version mismatch")
        fit_date = str(artifact.get("fit_date") or "").strip()
        date.fromisoformat(fit_date)
        cells = artifact.get("cells")
        if not isinstance(cells, Mapping):
            raise ValueError("day0 remaining bias artifact has no cells")
        parsed: dict[str, tuple[bool, float, dict[str, float]]] = {}
        for key, cell in cells.items():
            if not isinstance(cell, Mapping):
                raise ValueError(f"day0 remaining bias cell {key!r} malformed")
            stations = cell.get("stations") or {}
            if not isinstance(stations, Mapping):
                raise ValueError(f"day0 remaining bias cell {key!r} stations malformed")
            parsed[str(key)] = (
                cell.get("active") is True,
                _shift_value(cell["b_c"]),
                {str(city): _shift_value(value) for city, value in stations.items()},
            )
        self._cells = parsed
        self._fit_date = fit_date
        self._identity = identity

    @property
    def fit_date(self) -> str:
        return self._fit_date

    @property
    def identity(self) -> str:
        return self._identity

    def shift(self, *, city: str, metric: str, local_hour: float) -> Day0RemainingBias:
        cell = self._cells.get(cell_key(metric, local_hour))
        if cell is None or not cell[0]:
            return Day0RemainingBias(0.0, INACTIVE_CELL, self._identity)
        _active, pooled, stations = cell
        return Day0RemainingBias(stations.get(city, pooled), APPLIED, self._identity)


def artifact_path() -> Path:
    """``state/day0_remaining_center_bias.json`` under the runtime state directory."""

    from src.config import state_path

    return Path(state_path(ARTIFACT_FILENAME))


_cache_lock = threading.Lock()
_cached: tuple[str, int, RemainingBiasTable | None] | None = None
_logged_faults: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    if key in _logged_faults:
        return
    _logged_faults.add(key)
    _LOG.warning(message, *args)


def _load_table() -> RemainingBiasTable | None:
    """The artifact at ``artifact_path()``, cached on (path, mtime)."""

    global _cached
    path = artifact_path()
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    with _cache_lock:
        if _cached is not None and _cached[:2] == (str(path), mtime_ns):
            return _cached[2]
        table: RemainingBiasTable | None
        try:
            raw = path.read_bytes()
            identity = hashlib.sha256(raw).hexdigest()[:16]
            artifact = json.loads(raw)
            table = RemainingBiasTable(
                artifact, identity=f"{artifact.get('fit_date')}:{identity}"
            )
        except Exception as exc:  # noqa: BLE001 - malformed artifact serves the fallback
            _log_once("malformed", "day0_remaining_bias: unusable artifact at %s: %s", path, exc)
            table = None
        _cached = (str(path), mtime_ns, table)
        return table


def day0_remaining_bias(
    *,
    city: str,
    metric: str,
    decision_time: datetime,
    timezone_name: str,
) -> Day0RemainingBias:
    """The shift for one carrier build at ``decision_time``. Never raises.

    The one lookup shared by the materializer and every adapter rebuild, so the
    entry path and the held-position path cannot disagree about a cell.
    """

    try:
        table = _load_table()
        if table is None:
            return _UNAVAILABLE
        decided = decision_time.astimezone(timezone.utc)
        age_days = (decided.date() - date.fromisoformat(table.fit_date)).days
        if age_days < 0 or age_days > MAX_ARTIFACT_AGE_DAYS:
            _log_once(
                "age",
                "day0_remaining_bias: artifact fit_date %s is %d days from %s; unshifted",
                table.fit_date,
                age_days,
                decided.date(),
            )
            return _UNAVAILABLE
        local = decided.astimezone(ZoneInfo(timezone_name))
        return table.shift(
            city=city,
            metric=str(metric).strip().lower(),
            local_hour=local.hour + local.minute / 60.0,
        )
    except Exception as exc:  # noqa: BLE001 - the decision path keeps the live recipe
        _log_once("lookup", "day0_remaining_bias: lookup failed: %s", exc)
        return _UNAVAILABLE


def reset_cache() -> None:
    """Drop the module cache (tests that rewrite the artifact between assertions)."""

    global _cached
    with _cache_lock:
        _cached = None
        _logged_faults.clear()
