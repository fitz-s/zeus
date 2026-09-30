# Created: 2026-09-24
# Last reused or audited: 2026-09-30
# Authority basis: Day0 remaining-center settlement residual study 2026-09-24
#   (27,513 settled carrier rows: HIGH local 00-12 settled - mean member remaining max
#   = +0.27..+0.50 degC at a served width that already matches the residual sd);
#   2026-09-30 continuity repair (Helsinki 09-30 high: a band edge and a refit gate
#   flip each moved q(15C) 0.07 -> 0.45 on unchanged evidence).
#   Fitted by scripts/fit_day0_remaining_center_bias.py; applied by
#   src/data/day0_hourly_vectors.build_day0_remaining_probability_carrier.
"""Settlement-graded center bias of the Day0 remaining-day carrier.

WHAT IT CORRECTS. The remaining-hourly member extremes the shared Day0 carrier
integrates run cold or warm by an amount that depends on the local hour while its
width is right. The served value ``b(h)`` (degC) moves those member centers; nothing
else.

SHAPE. ``b`` is ONE continuous function of local hour ``h``: piecewise linear through
per-band node values placed at the band centres (01:00, 03:00, ..., 23:00 local) and
flat beyond the first and last centre. A band is only where evidence is pooled; it is
never a step in the served q. The same evidence at 01:59 and 02:01 serves shifts that
differ by at most (1/60 h) x the local slope.

SIZE. Every node value is already shrunk toward 0 by its own uncertainty in the fitter
(normal prior centred at 0, variance from the settled likelihood curvature, clustered
by city-day): a thin or noisy band contributes almost nothing, a well-measured band
contributes almost all of its estimate. There is no activation gate, so a daily refit
moves ``b`` by the change in evidence, never by a binary flip.

WHERE IT APPLIES. ``b`` is added to the remaining-hourly member centers before the
observed-boundary max/min and the settlement integration, identically in the point
q and the confidence draws (the carrier builder owns both). The observed extreme and
the separately typed final-daily provider centers are never shifted. The persisted
``day0_remaining_carrier_future_extremes_c`` stays the UNSHIFTED member vector: it is
the residual basis the fitter reads, and a fit on already-shifted centers would
measure its own correction and unwind it.

QUALIFIED FALLBACK, NOT FAIL-OPEN. A missing, stale, malformed, older-schema or
future-dated artifact serves shift 0 with status ``artifact_unavailable``. Shift 0 is
acceptable only because the unshifted recipe is the fallback the carrier has always
had. Every carrier stamps the status, so an absent artifact is visible in provenance,
never silent. This module never raises into the decision path and never reads a
database.

WALK-FORWARD. The fitter trains on settled days strictly before ``fit_date`` whose
labels were known before that date began (UTC). An artifact whose ``fit_date`` lies
after the decision date could have seen the decision day's outcome and is refused.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

_LOG = logging.getLogger("zeus.day0_remaining_bias")

ARTIFACT_FILENAME = "day0_remaining_center_bias.json"
# 2: one shrunk node curve per metric, served by linear interpolation. 1 was a gated
# 2-hour step table and is refused (a boot refit replaces it).
SCHEMA_VERSION = 2
# Evidence is pooled per 2-hour local band; each band's shrunk value is a node at the
# band centre, and the served shift interpolates linearly between nodes.
BAND_HOURS = 2
NODE_HOURS = tuple(band + BAND_HOURS / 2.0 for band in range(0, 24, BAND_HOURS))
# Sanity rail on a node value, not a tuning knob: the study's largest band is
# +0.50 degC. A value past this is a unit flip or a corrupted settlement row, and the
# whole artifact is refused rather than served.
MAX_ABS_SHIFT_C = 2.0
# The refit is daily. A week without a successful refit means the producer is broken;
# the shift is then no longer current evidence and the unshifted recipe serves.
MAX_ARTIFACT_AGE_DAYS = 7

APPLIED = "applied"
# The artifact carries no curve for this metric (the fitter saw none of its rows).
INACTIVE_CELL = "inactive_cell"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"


def band_of(local_hour: float) -> int:
    """The 2-hour band that pools fitting evidence for a local clock hour in [0, 24)."""

    return int(local_hour) // BAND_HOURS * BAND_HOURS


def interpolate_nodes(nodes: Sequence[float], local_hour: float) -> float:
    """The node curve at ``local_hour``: linear between band centres, flat past the ends."""

    if local_hour <= NODE_HOURS[0]:
        return float(nodes[0])
    if local_hour >= NODE_HOURS[-1]:
        return float(nodes[-1])
    right = bisect.bisect_right(NODE_HOURS, local_hour)
    x0, x1 = NODE_HOURS[right - 1], NODE_HOURS[right]
    y0, y1 = float(nodes[right - 1]), float(nodes[right])
    return y0 + (local_hour - x0) / (x1 - x0) * (y1 - y0)


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


def _nodes(raw: object, where: str) -> tuple[float, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError(f"day0 remaining bias {where} nodes malformed")
    values = tuple(float(value) for value in raw)
    if len(values) != len(NODE_HOURS) or not all(
        math.isfinite(v) and abs(v) <= MAX_ABS_SHIFT_C for v in values
    ):
        raise ValueError(f"day0 remaining bias {where} nodes out of range: {raw!r}")
    return values


class RemainingBiasTable:
    """Validated node curves over the fitted artifact. Pure; no I/O.

    Every metric the fitter saw carries a curve; a metric with no curve serves 0
    (``inactive_cell``), the value the fitter's shrinkage gives an unmeasured band.
    """

    __slots__ = ("_curves", "_fit_date", "_identity")

    def __init__(self, artifact: Mapping[str, Any], *, identity: str) -> None:
        if int(artifact.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError("day0 remaining bias artifact schema_version mismatch")
        fit_date = str(artifact.get("fit_date") or "").strip()
        date.fromisoformat(fit_date)
        metrics = artifact.get("metrics")
        if not isinstance(metrics, Mapping):
            raise ValueError("day0 remaining bias artifact has no metrics")
        curves: dict[str, tuple[tuple[float, ...], dict[str, tuple[float, ...]]]] = {}
        for metric, curve in metrics.items():
            if not isinstance(curve, Mapping):
                raise ValueError(f"day0 remaining bias metric {metric!r} malformed")
            stations = curve.get("stations") or {}
            if not isinstance(stations, Mapping):
                raise ValueError(f"day0 remaining bias metric {metric!r} stations malformed")
            curves[str(metric)] = (
                _nodes(curve.get("nodes_c"), str(metric)),
                {str(city): _nodes(v, f"{metric}/{city}") for city, v in stations.items()},
            )
        self._curves = curves
        self._fit_date = fit_date
        self._identity = identity

    @property
    def fit_date(self) -> str:
        return self._fit_date

    @property
    def identity(self) -> str:
        return self._identity

    def shift(self, *, city: str, metric: str, local_hour: float) -> Day0RemainingBias:
        curve = self._curves.get(metric)
        if curve is None:
            return Day0RemainingBias(0.0, INACTIVE_CELL, self._identity)
        pooled, stations = curve
        return Day0RemainingBias(
            interpolate_nodes(stations.get(city, pooled), local_hour), APPLIED, self._identity
        )


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
