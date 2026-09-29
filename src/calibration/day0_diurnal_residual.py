# Created: 2026-09-04
# Last reused or audited: 2026-09-29
# Authority basis: diurnal-residual study 2026-09-04 (histogram estimator) and the
#   2026-09-28 walk-forward of the survival-preserving mixture (city-day cluster
#   bootstrap, 72,681 settled served Day0 posteriors, ALL dLL -0.072 [-0.085, -0.060],
#   every metric x hours-after-noon slice negative). Fitted by
#   scripts/fit_day0_diurnal_residual.py; applied to every Day0 simplex row by
#   docs/authority/replacement_final_form_2026_06_09.md §1e "Day0 diurnal-residual
#   mixture".
"""Station diurnal-residual evidence for the Day0 final extreme, served INSIDE q.

WHAT IT MODELS. Before the diurnal peak the remaining-path carrier treats the rest of
the day's NWP path as near-certain and over-states the running-extreme ("floor") bin.
The station's own history says how far the extreme still moves from the running value
at this hour: the residual

    D = final_extreme - running_extreme        (absorbing direction; D >= 0)

counted per (metric, settlement unit, k = hours to the city's median extreme hour) and
per (metric, city, k), Empirical-Bayes shrunk pooled -> city with prior weight 25 and a
+0.5 Laplace floor on the pooled histogram.

HOW IT ENTERS q. ``H = max(H_confirmed, H_remaining)``. Conditional on the observed
boundary surviving -- i.e. on the bins the boundary has not already passed -- the
remaining extreme is a mixture ``(1 - w) provider path + w (A + D)``, A the running
extreme on the city's settlement grid. Bins the boundary has passed ("dead" bins) keep
their q untouched: that mass is the carrier's own statement that the boundary is
revised away, which this evidence does not speak to. Per simplex row r:

    m        = sum_{dead} r
    r'[dead] = r[dead]
    r'[live] = (1 - w) r[live] + w (1 - m) pi[live]      (pi sums to 1 over live bins)

One pure function (``Day0DiurnalMixture.apply``) transforms the point row AND every
draw row, so point q, bounds and every confidence draw describe one probability world.
``w`` is fitted per (metric, k bucket) by settled likelihood of this same operator on
BASE (unmixed) served q; a cell with fewer than MIN_WEIGHT_ROWS rows serves w = 0.

WALK-FORWARD. An artifact with ``fit_date`` T serves decisions on or after T. Its
counts come from station-days before T-1; its weights from settled posteriors dated
[T-31, T-2] scored against counts from station-days before T-32, so no weight row's own
station-day sits inside the pmf that scored it.

QUALIFIED FALLBACK. Missing, malformed, stale or future-dated artifact, unknown city,
unit disagreement, empty cell: the mixture is unavailable, the carrier q serves
unchanged, and provenance names the status. The unmixed recipe is acceptable only
because it is the one already live. This module never raises into the decision path
and never reads a database.
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
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

_LOG = logging.getLogger("zeus.day0_diurnal_residual")

ARTIFACT_FILENAME = "day0_diurnal_residual.json"
SCHEMA_VERSION = 2

# Residual cells in native settlement degrees. 40 covers the observed Fahrenheit tail
# (2.3M station-hours: 30 rows at D >= 40); the last cell is the open tail ">= J_MAX".
J_MAX = 40
PRIOR_WEIGHT = 25.0
# A (metric, k bucket) weight is served only on at least this many settled rows.
MIN_WEIGHT_ROWS = 200
# Refit is daily; past this age the evidence is no longer current.
MAX_ARTIFACT_AGE_DAYS = 14

APPLIED = "applied"
INACTIVE_CELL = "inactive_cell"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"
NOT_APPLICABLE = "not_applicable"


def k_bucket(k: int) -> int:
    """Weight cell of an hours-to-peak offset: -3..3 each, 4 for 4..7, 8 for >= 8."""

    if k < 4:
        return max(-3, k)
    return 4 if k < 8 else 8


def weight_key(metric: str, k: int) -> str:
    return f"{metric}|{k_bucket(k)}"


@dataclass(frozen=True, slots=True)
class Day0DiurnalMixture:
    """The fitted evidence for one Day0 family at one probability clock.

    Applying it is pure arithmetic over the carrier row; everything that decides the
    numbers is in these fields, so a persisted instance replays byte-for-byte.
    """

    weight: float
    pi: tuple[float, ...]
    dead: tuple[bool, ...]
    k: int
    anchor: float
    fit_date: str
    artifact: str

    @property
    def status(self) -> str:
        return APPLIED if self.weight > 0.0 else INACTIVE_CELL

    def apply(self, row: Sequence[float]) -> list[float]:
        """The mixed simplex row. Identity when w = 0."""

        if len(row) != len(self.pi):
            raise ValueError("DAY0_DIURNAL_MIXTURE_ROW_SHAPE_MISMATCH")
        values = [float(value) for value in row]
        if self.weight <= 0.0:
            return values
        dead_mass = sum(value for value, dead in zip(values, self.dead) if dead)
        live_mass = max(0.0, 1.0 - dead_mass)
        w = self.weight
        return [
            value if dead else (1.0 - w) * value + w * live_mass * pi
            for value, dead, pi in zip(values, self.dead, self.pi)
        ]

    def to_payload(self) -> dict[str, object]:
        return {
            "weight": self.weight,
            "pi": list(self.pi),
            "dead": list(self.dead),
            "k": self.k,
            "anchor": self.anchor,
            "fit_date": self.fit_date,
            "artifact": self.artifact,
        }

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> "Day0DiurnalMixture":
        pi = tuple(float(value) for value in raw["pi"])
        dead = tuple(bool(value) for value in raw["dead"])
        weight = float(raw["weight"])
        if len(pi) != len(dead) or not 0.0 <= weight <= 1.0:
            raise ValueError("DAY0_DIURNAL_MIXTURE_PAYLOAD_INVALID")
        return cls(
            weight=weight,
            pi=pi,
            dead=dead,
            k=int(raw["k"]),
            anchor=float(raw["anchor"]),
            fit_date=str(raw["fit_date"]),
            artifact=str(raw["artifact"]),
        )

    def identity(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]

    def provenance(self) -> dict[str, object]:
        return {
            "day0_diurnal_mixture_status": self.status,
            "day0_diurnal_mixture_weight": self.weight,
            "day0_diurnal_mixture_k": self.k,
            "day0_diurnal_mixture_anchor": self.anchor,
            "day0_diurnal_mixture_artifact": self.artifact,
            "day0_diurnal_mixture_identity": self.identity(),
            "day0_diurnal_mixture": self.to_payload(),
        }


def unavailable_provenance(status: str) -> dict[str, object]:
    return {"day0_diurnal_mixture_status": status}


class DiurnalResidualNowcast:
    """Pure lookup over the fitted artifact. No I/O."""

    __slots__ = ("_pooled", "_city", "_peak", "_trough", "_unit", "_weights", "_fit_date", "_identity")

    def __init__(self, artifact: Mapping[str, Any], *, identity: str = "") -> None:
        if int(artifact.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError("day0 diurnal residual artifact schema_version mismatch")
        if int(artifact.get("j_max", J_MAX)) != J_MAX:
            raise ValueError("day0 diurnal residual artifact j_max mismatch")
        fit_date = str(artifact.get("fit_date") or "").strip()
        date.fromisoformat(fit_date)
        self._fit_date = fit_date
        self._identity = identity or fit_date
        self._pooled = _counts_table(artifact.get("pooled"))
        self._city = _counts_table(artifact.get("city"))
        if not self._pooled:
            raise ValueError("day0 diurnal residual artifact has no pooled cells")
        self._peak = _float_map(artifact.get("peak_hours"))
        self._trough = _float_map(artifact.get("trough_hours"))
        self._unit = {
            str(city): str(unit).strip().upper()
            for city, unit in (artifact.get("unit") or {}).items()
        }
        weights: dict[str, float] = {}
        for key, cell in (artifact.get("weights") or {}).items():
            w = float(cell["w"])
            if not 0.0 <= w <= 1.0 or int(cell["n"]) < MIN_WEIGHT_ROWS:
                raise ValueError(f"day0 diurnal residual weight cell {key!r} invalid")
            weights[str(key)] = w
        self._weights = weights

    @property
    def fit_date(self) -> str:
        return self._fit_date

    @property
    def identity(self) -> str:
        return self._identity

    def anchor_hour(self, city: str, metric: str) -> float | None:
        """The city's median first-attainment hour for this metric's extreme."""

        return (self._peak if metric == "high" else self._trough).get(city)

    def fitted_unit(self, city: str) -> str | None:
        return self._unit.get(city)

    def hours_to_peak(self, city: str, metric: str, local_hour: float) -> int | None:
        """k = round(anchor_hour - round(local_hour)); positive means the peak is ahead."""

        anchor = self.anchor_hour(city, metric)
        if anchor is None or not math.isfinite(local_hour):
            return None
        return int(round(anchor - round(local_hour)))

    def weight(self, metric: str, k: int) -> float:
        return self._weights.get(weight_key(metric, k), 0.0)

    def pmf(
        self, *, city: str, metric: str, unit: str, local_hour: float
    ) -> list[float] | None:
        """P(D = j) for j in 0..J_MAX (the last cell is the tail >= J_MAX)."""

        if metric not in ("high", "low"):
            return None
        fitted = self._unit.get(city)
        if fitted is not None and fitted != unit:
            return None
        k = self.hours_to_peak(city, metric, local_hour)
        if k is None:
            return None
        pooled = self._pooled.get(_key(metric, unit, k))
        if pooled is None or sum(pooled) <= 0:
            return None
        denominator = sum(pooled) + 0.5 * (J_MAX + 1)
        base = [(count + 0.5) / denominator for count in pooled]
        city_counts = self._city.get(_key(metric, city, k))
        if city_counts is not None and sum(city_counts) > 0:
            city_n = sum(city_counts)
            base = [
                (city_counts[j] + PRIOR_WEIGHT * base[j]) / (city_n + PRIOR_WEIGHT)
                for j in range(J_MAX + 1)
            ]
        total = sum(base)
        if not math.isfinite(total) or total <= 0.0:
            return None
        return [value / total for value in base]

    def mixture(
        self,
        *,
        city: str,
        metric: str,
        unit: str,
        local_hour: float,
        running_extreme: float,
        bin_bounds: Sequence[tuple[float | None, float | None]],
        round_to_grid: Callable[[float], float],
        weight: float | None = None,
    ) -> Day0DiurnalMixture | None:
        """The mixture for one family, or None when the evidence cannot be served.

        ``running_extreme`` and ``bin_bounds`` are native settlement degrees;
        ``round_to_grid`` is the city's settlement rounding
        (``SettlementSemantics.round_single``), which places the anchor A. ``weight``
        overrides the fitted cell weight (the fitter scores candidate weights with this
        same operator).
        """

        if not math.isfinite(running_extreme) or not bin_bounds:
            return None
        pmf = self.pmf(city=city, metric=metric, unit=unit, local_hour=local_hour)
        k = self.hours_to_peak(city, metric, local_hour)
        if pmf is None or k is None:
            return None
        anchor = float(round_to_grid(running_extreme))
        dead: list[bool] = []
        mass: list[float] = []
        for low, high in bin_bounds:
            if low is None and high is None:
                return None
            if metric == "high":
                is_dead = high is not None and high < anchor - 1e-9
                j_low = None if low is None else low - anchor
                j_high = None if high is None else high - anchor
            else:
                is_dead = low is not None and low > anchor + 1e-9
                j_low = None if high is None else anchor - high
                j_high = None if low is None else anchor - low
            dead.append(is_dead)
            first = 0 if j_low is None else max(0, int(math.ceil(j_low - 1e-9)))
            last = J_MAX if j_high is None else min(J_MAX, int(math.floor(j_high + 1e-9)))
            mass.append(0.0 if is_dead or first > last else sum(pmf[first : last + 1]))
        total = sum(mass)
        if total <= 0.0 or not math.isfinite(total):
            return None
        served = self.weight(metric, k) if weight is None else float(weight)
        return Day0DiurnalMixture(
            weight=served,
            pi=tuple(value / total for value in mass),
            dead=tuple(dead),
            k=k,
            anchor=anchor,
            fit_date=self._fit_date,
            artifact=self._identity,
        )


def _key(*parts: object) -> str:
    return "|".join(str(part) for part in parts)


def _counts_table(raw: object) -> dict[str, list[int]]:
    if not isinstance(raw, Mapping):
        return {}
    table: dict[str, list[int]] = {}
    for key, counts in raw.items():
        if not isinstance(counts, Sequence) or isinstance(counts, (str, bytes)):
            continue
        if len(counts) != J_MAX + 1:
            continue
        table[str(key)] = [int(value) for value in counts]
    return table


def _float_map(raw: object) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            out[str(key)] = parsed
    return out


def artifact_path() -> Path:
    """``state/day0_diurnal_residual.json`` under the runtime state directory."""

    from src.config import state_path

    return Path(state_path(ARTIFACT_FILENAME))


_cache_lock = threading.Lock()
_cached: tuple[str, int, DiurnalResidualNowcast | None] | None = None
_logged_faults: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    if key in _logged_faults:
        return
    _logged_faults.add(key)
    _LOG.warning(message, *args)


def _load_nowcast() -> DiurnalResidualNowcast | None:
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
        nowcast: DiurnalResidualNowcast | None
        try:
            raw = path.read_bytes()
            artifact = json.loads(raw)
            nowcast = DiurnalResidualNowcast(
                artifact,
                identity=f"{artifact.get('fit_date')}:{hashlib.sha256(raw).hexdigest()[:16]}",
            )
        except Exception as exc:  # noqa: BLE001 - malformed artifact serves the fallback
            _log_once("malformed", "day0_diurnal_residual: unusable artifact at %s: %s", path, exc)
            nowcast = None
        _cached = (str(path), mtime_ns, nowcast)
        return nowcast


def day0_diurnal_mixture(
    *,
    city: str,
    metric: str,
    unit: str,
    decision_time: datetime,
    timezone_name: str,
    running_extreme: float | None,
    bin_bounds: Sequence[tuple[float | None, float | None]],
    round_to_grid: Callable[[float], float],
) -> tuple[Day0DiurnalMixture | None, dict[str, object]]:
    """The served mixture for one Day0 family, plus its provenance. Never raises.

    The one lookup shared by the materializer and every reactor rebuild, so the
    posterior, ENTRY, held redecision and submit reproduction cannot disagree about
    the evidence.
    """

    try:
        nowcast = _load_nowcast()
        if nowcast is None:
            return None, unavailable_provenance(ARTIFACT_UNAVAILABLE)
        decided = decision_time.astimezone(timezone.utc)
        age_days = (decided.date() - date.fromisoformat(nowcast.fit_date)).days
        if age_days < 0 or age_days > MAX_ARTIFACT_AGE_DAYS:
            _log_once(
                "age",
                "day0_diurnal_residual: artifact fit_date %s is %d days from %s; unmixed",
                nowcast.fit_date,
                age_days,
                decided.date(),
            )
            return None, unavailable_provenance(ARTIFACT_UNAVAILABLE)
        if running_extreme is None:
            return None, unavailable_provenance(NOT_APPLICABLE)
        local = decided.astimezone(ZoneInfo(timezone_name))
        mixture = nowcast.mixture(
            city=city,
            metric=str(metric).strip().lower(),
            unit=str(unit).strip().upper(),
            local_hour=local.hour + local.minute / 60.0,
            running_extreme=float(running_extreme),
            bin_bounds=bin_bounds,
            round_to_grid=round_to_grid,
        )
        if mixture is None:
            return None, unavailable_provenance(INACTIVE_CELL)
        return mixture, mixture.provenance()
    except Exception as exc:  # noqa: BLE001 - the decision path keeps the carrier q
        _log_once("lookup", "day0_diurnal_residual: lookup failed: %s", exc)
        return None, unavailable_provenance(ARTIFACT_UNAVAILABLE)


def reset_cache() -> None:
    """Drop the module cache (tests that rewrite the artifact between assertions)."""

    global _cached
    with _cache_lock:
        _cached = None
        _logged_faults.clear()
