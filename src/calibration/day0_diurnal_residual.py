# Created: 2026-09-04
# Last reused or audited: 2026-09-04
# Authority basis: diurnal-residual study 2026-09-04 (scratchpad/diurnal/REPORT.md §5
#   "Recommended model form" + dist_nowcast.py::DistNowcast). Fitted by
#   scripts/fit_day0_diurnal_residual.py; served as a VETO on the Day0 entry path
#   (src/engine/day0_admission.py), never as a q source.
"""Station diurnal-residual nowcast for the Day0 entry lane (read-only, fail-open).

WHAT IT MODELS. On the target day our posterior conditions on the running observed
extreme but treats the remaining NWP path as near-certain, so it is overconfident on
the running-extreme ("floor") bin before the diurnal peak: at local hours 08-11 with
the peak 0-1h away a stated q_floor of 0.90-0.95 realises 0.31. This module carries the
empirical distribution of the residual

    D = final_extreme - running_extreme        (absorbing direction; D >= 0)

as counts per (metric, k = hours-to-peak, NWP-gap band) and per (metric, city, k),
Empirical-Bayes shrunk pooled -> gap cell -> city cell with prior weight 25 and a +0.5
Laplace floor on the pooled histogram.

WHY A VETO AND NOT A q. Walk-forward on identical candidate (slice, bin) pairs the
nowcast's executable edge beats our posterior's (+0.028 vs +0.017 per unit on HIGH),
but its Brier is significantly WORSE than the market's at every hour (0.2097 vs 0.1051,
CI [-0.1445, -0.0573]). It is a better filter than us and a worse forecaster than the
book, so the only sound wiring is to refuse the trades it disagrees with. The set "our
model would trade, the nowcast vetoes" is -0.020/unit HIGH [-0.040, -0.001] and
-0.043/unit LOW, negative in 6/6 out-of-sample windows.

WALK-FORWARD AT SERVE TIME. The study filtered every histogram to station-days
strictly before the target date at query time. Here that filter is baked into the
artifact instead: the fitter drops every record whose date is >= its ``fit_date``, so a
decision made on day T reads counts assembled only from days before the last refit.
Serving therefore does no date filtering at all — and MUST not, because the artifact
cannot distinguish a record it kept from one it should have dropped. The freshness gate
below is what keeps ``fit_date`` close enough to T for that to be a real bound rather
than a stale one.

FAIL-OPEN IS THE WHOLE CONTRACT. Missing artifact, malformed artifact, artifact older
than MAX_ARTIFACT_AGE_DAYS, unknown city, unit disagreement, anchor absent, empty cell
-- every one returns None and the gate above stays inert. The artifact's PRESENCE is
the switch; there is no config flag. This module never raises into the decision path
and never reads a database.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.contracts.settlement_semantics import round_wmo_half_up_value

_LOG = logging.getLogger("zeus.day0_diurnal_residual")

ARTIFACT_FILENAME = "day0_diurnal_residual.json"
SCHEMA_VERSION = 1

# The residual is capped at +12 native degrees: beyond that the cell is empty in
# 2.2M station-hours and the tail mass belongs on the last bucket anyway.
J_MAX = 12
# Empirical-Bayes prior weight for both shrink stages (study: prior=25.0).
PRIOR_WEIGHT = 25.0
# A gap cell tilts the pooled histogram only once it has this many rows.
GAP_MIN_ROWS = 30
# NWP gap = NWP center - running extreme (high; reversed for low), bucketed.
GAP_BAND_EDGES: tuple[tuple[float, float], ...] = (
    (-math.inf, -0.5),
    (-0.5, 0.5),
    (0.5, 1.5),
    (1.5, 2.5),
    (2.5, 4.5),
    (4.5, math.inf),
)
# Past this age the artifact's walk-forward bound has drifted too far from the
# decision date to mean anything; the gate goes inert rather than acting on it.
MAX_ARTIFACT_AGE_DAYS = 14


def gap_band_index(gap: float) -> int | None:
    """Index of the NWP-gap band containing ``gap``, or None when non-finite."""

    if not math.isfinite(gap):
        return None
    for index, (low, high) in enumerate(GAP_BAND_EDGES):
        if low <= gap < high:
            return index
    return None


@dataclass(frozen=True, slots=True)
class NowcastVerdict:
    """The nowcast's probability that the candidate's HELD token pays, plus provenance."""

    q_held: float
    basis: str  # "pooled" | "gap" | "city" — the most specific shrink stage applied
    fit_date: str


class DiurnalResidualNowcast:
    """Pure lookup over the fitted residual-count artifact. No I/O, stdlib only."""

    __slots__ = ("_pooled", "_gap", "_city", "_peak", "_trough", "_unit", "_fit_date")

    def __init__(self, artifact: Mapping[str, Any]) -> None:
        if int(artifact.get("schema_version", 0)) != SCHEMA_VERSION:
            raise ValueError("day0 diurnal residual artifact schema_version mismatch")
        fit_date = str(artifact.get("fit_date") or "").strip()
        # Parsed here so a malformed date is a construction error, not a serve-time one.
        date.fromisoformat(fit_date)
        self._fit_date = fit_date
        self._pooled = _counts_table(artifact.get("pooled"))
        self._gap = _counts_table(artifact.get("gap"))
        self._city = _counts_table(artifact.get("city"))
        if not self._pooled:
            raise ValueError("day0 diurnal residual artifact has no pooled cells")
        self._peak = _float_map(artifact.get("peak_hours"))
        self._trough = _float_map(artifact.get("trough_hours"))
        self._unit = {
            str(city): str(unit).strip().upper()
            for city, unit in (artifact.get("unit") or {}).items()
        }

    @property
    def fit_date(self) -> str:
        return self._fit_date

    def anchor_hour(self, city: str, metric: str) -> float | None:
        """The city's median first-attainment hour for this metric's extreme."""

        table = self._peak if metric == "high" else self._trough
        return table.get(city)

    def fitted_unit(self, city: str) -> str | None:
        return self._unit.get(city)

    def hours_to_peak(self, city: str, metric: str, local_hour: float) -> int | None:
        """k = round(anchor_hour - round(local_hour)); positive means the peak is ahead."""

        anchor = self.anchor_hour(city, metric)
        if anchor is None or not math.isfinite(local_hour):
            return None
        return int(round(anchor - round(local_hour)))

    def pmf(
        self,
        *,
        city: str,
        metric: str,
        local_hour: float,
        gap: float | None = None,
    ) -> tuple[list[float], str] | None:
        """P(D = j) for j in 0..J_MAX, plus the most specific shrink stage applied.

        Pooled (metric, k) histogram with a +0.5 Laplace floor, tilted by the
        (metric, k, gapband) histogram when that cell has >= GAP_MIN_ROWS rows, then by
        the city's own (metric, k) histogram. Both tilts use prior weight PRIOR_WEIGHT.
        None when the pooled cell is empty or the city has no anchor.
        """

        if metric not in ("high", "low"):
            return None
        k = self.hours_to_peak(city, metric, local_hour)
        if k is None:
            return None
        pooled = self._pooled.get(_key(metric, k))
        if pooled is None:
            return None
        pooled_n = sum(pooled)
        if pooled_n <= 0:
            return None
        denominator = pooled_n + 0.5 * (J_MAX + 1)
        base = [(count + 0.5) / denominator for count in pooled]
        basis = "pooled"
        if gap is not None:
            band = gap_band_index(gap)
            if band is not None:
                gap_counts = self._gap.get(_key(metric, k, band))
                if gap_counts is not None:
                    gap_n = sum(gap_counts)
                    if gap_n >= GAP_MIN_ROWS:
                        base = [
                            (gap_counts[j] + PRIOR_WEIGHT * base[j])
                            / (gap_n + PRIOR_WEIGHT)
                            for j in range(J_MAX + 1)
                        ]
                        basis = "gap"
        city_counts = self._city.get(_key(metric, city, k))
        if city_counts is not None:
            city_n = sum(city_counts)
            if city_n > 0:
                base = [
                    (city_counts[j] + PRIOR_WEIGHT * base[j]) / (city_n + PRIOR_WEIGHT)
                    for j in range(J_MAX + 1)
                ]
                basis = "city"
        total = sum(base)
        if not math.isfinite(total) or total <= 0.0:
            return None
        return [value / total for value in base], basis

    def bin_probability(
        self,
        *,
        city: str,
        metric: str,
        local_hour: float,
        running_extreme: float,
        bin_low: float | None,
        bin_high: float | None,
        gap: float | None = None,
    ) -> tuple[float, str] | None:
        """P(final extreme settles inside [bin_low, bin_high]) under the residual pmf.

        Offsets are taken against the running extreme on the settlement integer grid:
        for HIGH the final is ``round(running) + j``, for LOW it is ``round(running) -
        j``, j >= 0 by absorption. A point bin therefore reads one cell; a range bin
        sums the cells it spans; an open-ended top (HIGH) or bottom (LOW) bin sums the
        tail through J_MAX. A bin the absorbing direction has already passed gets 0.
        """

        if bin_low is None and bin_high is None:
            return None
        if not math.isfinite(running_extreme):
            return None
        result = self.pmf(city=city, metric=metric, local_hour=local_hour, gap=gap)
        if result is None:
            return None
        pmf, basis = result
        # WMO half-up (fitter's grid law -- src/contracts/settlement_semantics.py), not
        # Python's banker's round(): they disagree at a running extreme ending in .5
        # (real for an ogimet native-tenths ledger), which would silently misalign the
        # served anchor against the histogram cell the fitter counted it into.
        anchor = int(round_wmo_half_up_value(running_extreme))
        if metric == "high":
            # final = anchor + j  =>  bin_low <= anchor + j <= bin_high
            j_low = None if bin_low is None else bin_low - anchor
            j_high = None if bin_high is None else bin_high - anchor
        else:
            # final = anchor - j  =>  bin_low <= anchor - j <= bin_high
            j_low = None if bin_high is None else anchor - bin_high
            j_high = None if bin_low is None else anchor - bin_low
        first = 0 if j_low is None else max(0, int(math.ceil(j_low - 1e-9)))
        last = J_MAX if j_high is None else min(J_MAX, int(math.floor(j_high + 1e-9)))
        if first > last:
            return 0.0, basis
        return sum(pmf[first : last + 1]), basis

    def held_probability(
        self,
        *,
        city: str,
        metric: str,
        direction: str,
        local_hour: float,
        running_extreme: float,
        bin_low: float | None,
        bin_high: float | None,
        gap: float | None = None,
    ) -> NowcastVerdict | None:
        """The nowcast's probability that the token this candidate would HOLD pays."""

        side = str(direction or "").strip().lower()
        if side not in ("buy_yes", "buy_no"):
            return None
        result = self.bin_probability(
            city=city,
            metric=metric,
            local_hour=local_hour,
            running_extreme=running_extreme,
            bin_low=bin_low,
            bin_high=bin_high,
            gap=gap,
        )
        if result is None:
            return None
        q_yes, basis = result
        q_held = q_yes if side == "buy_yes" else 1.0 - q_yes
        if not math.isfinite(q_held):
            return None
        return NowcastVerdict(
            q_held=min(1.0, max(0.0, q_held)),
            basis=basis,
            fit_date=self._fit_date,
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
_cached_nowcast: DiurnalResidualNowcast | None = None
_cached_mtime_ns: int | None = None
_cached_path: str | None = None
_logged_faults: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    """One WARNING per fault family per process — a missing artifact is the normal
    dormant state and must not fill the log at the decision cadence."""

    if key in _logged_faults:
        return
    _logged_faults.add(key)
    _LOG.warning(message, *args)


def load_day0_diurnal_residual_nowcast(
    *,
    now: datetime | None = None,
) -> DiurnalResidualNowcast | None:
    """The fitted nowcast, or None when absent / malformed / stale. Never raises.

    Module singleton keyed on the artifact's mtime, so a scheduled refit is picked up
    by a long-lived daemon without a restart while an unchanged file stays a cache hit.
    """

    global _cached_nowcast, _cached_mtime_ns, _cached_path
    try:
        path = artifact_path()
        path_text = str(path)
        mtime_ns = path.stat().st_mtime_ns
    except Exception:
        # Absent artifact is the dormant default, not an incident.
        with _cache_lock:
            _cached_nowcast = None
            _cached_mtime_ns = None
        return None
    with _cache_lock:
        if (
            _cached_nowcast is None
            or _cached_mtime_ns != mtime_ns
            or _cached_path != path_text
        ):
            try:
                artifact = json.loads(path.read_text(encoding="utf-8"))
                _cached_nowcast = DiurnalResidualNowcast(artifact)
            except Exception as exc:  # noqa: BLE001 — malformed artifact: stay dormant
                _log_once(
                    "malformed",
                    "day0_diurnal_residual: unusable artifact at %s: %s",
                    path_text,
                    exc,
                )
                _cached_nowcast = None
            _cached_mtime_ns = mtime_ns
            _cached_path = path_text
        nowcast = _cached_nowcast
    if nowcast is None:
        return None
    reference = (now or datetime.now(timezone.utc)).date()
    try:
        age_days = (reference - date.fromisoformat(nowcast.fit_date)).days
    except ValueError:
        return None
    if age_days > MAX_ARTIFACT_AGE_DAYS:
        _log_once(
            "stale",
            "day0_diurnal_residual: artifact fit_date %s is %d days old (> %d); gate inert",
            nowcast.fit_date,
            age_days,
            MAX_ARTIFACT_AGE_DAYS,
        )
        return None
    return nowcast


def reset_cache() -> None:
    """Drop the module cache (tests that rewrite the artifact between assertions)."""

    global _cached_nowcast, _cached_mtime_ns, _cached_path
    with _cache_lock:
        _cached_nowcast = None
        _cached_mtime_ns = None
        _cached_path = None
        _logged_faults.clear()
