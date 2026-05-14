"""Canonical calibration bin grids — decouple Platt training from market_events.

First-principles observation (2026-04-14 refactor)
--------------------------------------------------
Platt calibration learns `f(forecast, reality)`, not `f(forecast, market)`. The
historical pipeline joined `ensemble_snapshots`, `settlements`, and
`market_events` to construct training pairs, which meant that losing the market
bin structure (legacy predecessor data loss) silently broke calibration. But the bin
structure a calibration model sees at *training time* has nothing in common with
market operation — it is merely "the partition of the real line over which we
accumulate (P_raw, outcome) statistics". Any partition will do, provided it is:

1. **complete** — every plausible settlement value lands in exactly one bin
   (otherwise Σ P_raw < 1.0 at training time and Platt intercept drifts);
2. **consistent with live widths** — trained parameters transfer to live bins
   via ``ExtendedPlattCalibrator``'s width-normalized density space;
3. **anchored to integer semantics** — since ``SettlementSemantics`` always
   rounds to integers (WMO half-up), bin membership is unambiguous for real
   settlement values.

This module defines two such partitions — ``F_CANONICAL_GRID`` (92 bins) and
``C_CANONICAL_GRID`` (102 bins) — that mirror the live Polymarket widths
(2°F pair-integer interior bins, 1°C point-integer interior bins, plus
``"X° or below"`` / ``"Y° or higher"`` shoulders at both ends).

Why 2°F / 1°C widths
--------------------
Per user directive 2026-04-14 and verified in ``src/types/market.py:Bin``
``__post_init__``: Fahrenheit non-shoulder bins MUST have width 2 (two integer
settlement values, e.g. ``"39-40°F"`` = integers ``{39, 40}``), Celsius
non-shoulder bins MUST have width 1 (single integer, e.g. ``"15°C"`` =
``{15}``). The canonical grids produce labels that pass the ``Bin`` width
validator and round-trip through ``src/data/market_scanner.py::_parse_temp_range``
and ``src/calibration/store.py::infer_bin_width_from_label``.

Why odd-start interior for F
----------------------------
Interior F pairs are aligned ``{(-39,-38), (-37,-36), ..., (139,140)}``. Real
Polymarket F markets use BOTH odd-start (e.g. ``"39-40°F"``) and even-start
(e.g. ``"50-51°F"``) alignments depending on the per-market choice of center.
We train at a single fixed alignment because ``ExtendedPlattCalibrator``
(``src/calibration/platt.py:31 normalize_bin_probability_for_calibration``)
fits in **width-normalized density space** — learned parameters are functions
of per-degree density, not per-alignment bin mass. The learned `(A, B, C)`
generalize to any 2°F-wide bin regardless of alignment, so training alignment
is purely a sampling choice with no generalization penalty.

Why shoulders are essential
---------------------------
Without shoulders, a member forecast landing at 150°F silently leaks
probability mass (interior grid stops at 140°F). Training examples would have
``Σ P_raw < 1.0`` while live markets always sum to exactly 1.0 (Polymarket
always pads with ``"X or below"`` / ``"Y or higher"``). A systematic
intercept bias would corrupt every learned Platt model — subtly, and only
for cities experiencing extreme weather. Shoulders close the partition.

What this module does NOT do
----------------------------
- Compute P_raw. That is ``src/signal/ensemble_signal.py::p_raw_vector_from_maxes``,
  which must be the single source of truth (shared by live inference and
  offline rebuild). This module only provides the ``list[Bin]`` input.
- Read or write the database. Pure Python, no side effects.
- Define cluster / season stratification. Those remain in
  ``src/calibration/manager.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Literal

import numpy as np

from src.config import City
from src.types.market import Bin

Unit = Literal["F", "C"]


class UnitProvenanceError(Exception):
    """Raised when ensemble member values do not match the expected unit.

    Antibody for the "ensemble_snapshots has no explicit members_unit column"
    provenance hole. Caller asserts each JOINed snapshot passes this check
    before forwarding member values to the Monte Carlo path.
    """


# Sensible climatological envelopes per unit.
# These are wider than any plausible daily max — the goal is to catch
# cross-unit confusion (°F values mistaken for °C), not to validate
# physical correctness.
#
# Asymmetry note: the F range is necessarily wide because real °F daily
# maxes span roughly [-30, 130]. Unfortunately this means °C daily maxes
# (0-30 °C) fall inside the F plausible range — a °C-in-°F leak cannot
# be caught by a univariate median check alone. This is why
# ``validate_members_vs_observation`` exists: it anchors the plausibility
# check to the VERIFIED observation for the same (city, target_date),
# which closes the univariate asymmetry.
_F_PLAUSIBLE_RANGE = (-50.0, 140.0)
_C_PLAUSIBLE_RANGE = (-50.0, 55.0)

# Maximum |median(members) - observation| tolerated at 24h–7d lead.
#
# These tolerances are RELATIVE — `tol = min(cap, max(floor, k * ensemble_spread))`.
#
# 2026-05-12 floor revision (post-c8bb645a / post-Codex-P1 #111 empirical review):
#   The c8bb645a relative-tolerance fix assumed real cold-snap misses
#   produce WIDE ensemble spread (so `k * spread` dominates the floor).
#   Production rebuild on 2026-05-12 (PR #111 follow-up) disproved that
#   assumption: real confident-wrong arctic / extreme-cold misses have
#   NARROW spread (1.5–2.5 °C) because the ensemble is confidently warm
#   and reality is suddenly cold. The floor must therefore be wide enough
#   to admit these confident-wrong cases on its own. Empirical rejects:
#     Helsinki/2024-01-21:  offset=22.64 °C, spread=1.85 °C (arctic snap)
#     Munich/2026-01-12:    offset=10.89 °C, spread=2.07 °C
#     Moscow/2024-01-14:    offset=11.96 °C, spread=1.51 °C
#     London/2025-01-05:    offset=10.02 °C, spread=1.76 °C
#     Helsinki/2026-01-06:  offset=13.07 °C, spread=2.64 °C
#     Houston/2026-04-23:   offset=33.62 °F, spread=1.53 °F (April cold front)
#     Denver/2024-03-19:    offset=18.55 °F, spread=3.90 °F
#
# Trade-off: bumping floors weakens unit-error detection sensitivity, but
# canonical unit confusion still produces large offsets:
#   - °C-in-°F city (e.g. 20 °C interpreted as 20 °F vs obs 70 °F): ~50 °F offset
#   - °F-in-°C city (e.g. 70 °F interpreted as 70 °C vs obs 20 °C): ~50 °C offset
# So 25 °C / 40 °F floors still catch canonical unit errors with margin.
#
# The absolute CAP (Codex P1 #111) closes the wide-spread cross-unit leak:
# °C members [0,10,20,30,40] in an °F city against obs=75 °F have spread=40
# and offset=55; without a cap, k*spread=160 would let it through. With
# cap = 50 °F (28 °C), tol stays bounded and contamination is rejected.
# floor ≤ cap invariant: 25 ≤ 28 (°C), 40 ≤ 50 (°F).
#
# True structural fix (deferred): newtype `Celsius`/`Fahrenheit` so unit
# mixing is a TypeError at construction. Until then, this validator is a
# best-effort runtime guard that intentionally errs on the side of
# admitting extreme-weather data over false-positive unit rejection.
_F_VS_OBS_MAX_OFFSET_FLOOR = 40.0       # °F absolute floor (was 18 pre-2026-05-12)
_C_VS_OBS_MAX_OFFSET_FLOOR = 25.0       # °C absolute floor (was 10 pre-2026-05-12)
_F_VS_OBS_MAX_OFFSET_CAP = 50.0         # °F absolute upper cap (Codex P1 #111)
_C_VS_OBS_MAX_OFFSET_CAP = 28.0         # °C absolute upper cap (Codex P1 #111)
_VS_OBS_SPREAD_MULTIPLIER = 4.0         # tol = min(cap, max(floor, k * (max-min)))


@dataclass(frozen=True)
class CanonicalBinGrid:
    """A complete, non-overlapping partition of the real line into Bins.

    Implements a constant-time ``bin_for_value`` by precomputing the bin list
    once at construction. Instances are frozen and cached at module level;
    callers should not construct their own unless adding a new canonical
    variant.
    """

    unit: Unit
    label: str  # e.g. "F_canonical_v1"
    _bins: tuple[Bin, ...] = field(repr=False)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def fahrenheit_odd_start(
        cls,
        *,
        shoulder_low: int = -40,
        shoulder_high: int = 141,
    ) -> "CanonicalBinGrid":
        """Build the canonical F grid.

        Interior: 2°F pairs at odd-start alignment,
        ``(shoulder_low + 1, shoulder_low + 2), (shoulder_low + 3, shoulder_low + 4), ...``.
        The default values produce 90 interior pairs from (-39,-38) through
        (139,140), plus "-40°F or below" and "141°F or higher" shoulders —
        92 total bins.
        """
        if (shoulder_high - shoulder_low - 1) % 2 != 0:
            raise ValueError(
                f"Interior span ({shoulder_low + 1}..{shoulder_high - 1}) must be an even "
                f"number of integers to partition into 2-wide pairs"
            )
        bins: list[Bin] = []
        bins.append(Bin(
            low=None,
            high=float(shoulder_low),
            unit="F",
            label=f"{shoulder_low}°F or below",
        ))
        low = shoulder_low + 1
        while low + 1 <= shoulder_high - 1:
            high = low + 1
            bins.append(Bin(
                low=float(low),
                high=float(high),
                unit="F",
                label=f"{low}-{high}°F",
            ))
            low += 2
        bins.append(Bin(
            low=float(shoulder_high),
            high=None,
            unit="F",
            label=f"{shoulder_high}°F or higher",
        ))
        return cls(unit="F", label="F_canonical_v1", _bins=tuple(bins))

    @classmethod
    def celsius_point(
        cls,
        *,
        shoulder_low: int = -40,
        shoulder_high: int = 61,
    ) -> "CanonicalBinGrid":
        """Build the canonical C grid.

        Interior: 1°C point bins at every integer
        ``{shoulder_low + 1, ..., shoulder_high - 1}``.
        Default: 100 interior bins from -39°C through 60°C, plus
        "-40°C or below" and "61°C or higher" shoulders — 102 total bins.
        """
        if shoulder_high - shoulder_low - 1 < 1:
            raise ValueError(
                f"Interior must contain at least one integer; "
                f"got shoulder_low={shoulder_low}, shoulder_high={shoulder_high}"
            )
        bins: list[Bin] = []
        bins.append(Bin(
            low=None,
            high=float(shoulder_low),
            unit="C",
            label=f"{shoulder_low}°C or below",
        ))
        for v in range(shoulder_low + 1, shoulder_high):
            bins.append(Bin(
                low=float(v),
                high=float(v),
                unit="C",
                label=f"{v}°C",
            ))
        bins.append(Bin(
            low=float(shoulder_high),
            high=None,
            unit="C",
            label=f"{shoulder_high}°C or higher",
        ))
        return cls(unit="C", label="C_canonical_v1", _bins=tuple(bins))

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def as_bins(self) -> list[Bin]:
        """Return a fresh list of Bins for consumers (list[Bin] expected by p_raw_vector)."""
        return list(self._bins)

    def iter_bins(self) -> Iterator[Bin]:
        return iter(self._bins)

    @property
    def n_bins(self) -> int:
        return len(self._bins)

    def bin_for_value(self, v: float) -> Bin:
        """Which canonical bin contains v? Real-line exact partition.

        Semantics (per refactor plan RVW-4):
        - Low shoulder (``is_open_low``): matches v <= bin.high
        - High shoulder (``is_open_high``): matches v >= bin.low
        - Interior: matches bin.low <= v <= bin.high (inclusive-closed)

        Shoulders are checked first in iteration order to make boundary
        values (e.g. ``v == shoulder_low``) deterministic: they fall into
        the shoulder, never into the first interior bin.
        """
        for b in self._bins:
            if b.is_open_low and b.high is not None and v <= b.high:
                return b
            if b.is_open_high and b.low is not None and v >= b.low:
                return b
            if (
                not b.is_shoulder
                and b.low is not None
                and b.high is not None
                and b.low <= v <= b.high
            ):
                return b
        # Unreachable for a complete partition (shoulders handle all values
        # outside the interior range). Safety-net for future refactoring.
        raise ValueError(
            f"No canonical bin contains value v={v!r} in grid {self.label}. "
            f"This indicates a partition gap — check grid construction."
        )


# ----------------------------------------------------------------------
# Canonical grid constants — single source of truth
# ----------------------------------------------------------------------

F_CANONICAL_GRID: CanonicalBinGrid = CanonicalBinGrid.fahrenheit_odd_start()
C_CANONICAL_GRID: CanonicalBinGrid = CanonicalBinGrid.celsius_point()


def grid_for_city(city: City) -> CanonicalBinGrid:
    """Dispatch the canonical grid for a city by its settlement unit.

    Raises on any unit value outside ``{"F", "C"}`` to fail-loud on
    config drift — we do not silently fall back to a default.
    """
    if city.settlement_unit == "F":
        return F_CANONICAL_GRID
    if city.settlement_unit == "C":
        return C_CANONICAL_GRID
    raise ValueError(
        f"City {city.name!r} has unknown settlement_unit {city.settlement_unit!r}; "
        f"expected 'F' or 'C'"
    )


# ----------------------------------------------------------------------
# Unit-provenance antibody
# ----------------------------------------------------------------------


def validate_members_unit_plausible(
    member_maxes: np.ndarray,
    city: City,
) -> None:
    """Guard against silent unit drift in ``ensemble_snapshots.members_json``.

    The ``ensemble_snapshots`` table has no explicit ``members_unit`` column
    at the time of this refactor; members are assumed to share the city's
    ``settlement_unit`` because that's how the TIGGE ingestion pipeline
    currently writes them. This assumption is verified empirically (NYC = °F,
    Paris / Tokyo = °C, see 2026-04-14 DB spot check) but unenforced by
    schema.

    This function raises ``UnitProvenanceError`` when the member value
    distribution is implausible for the expected unit, catching the "°F
    values ingested into a °C city" failure mode at runtime. The ranges
    are wide (wider than any plausible daily max) — they are not
    temperature sanity checks, they are **cross-unit confusion detectors**.

    Long-term fix: add a ``members_unit`` column to ``ensemble_snapshots``
    during task #61 (TIGGE ingestion rewrite) and verify it at write time.
    """
    arr = np.asarray(member_maxes, dtype=float)
    if arr.size == 0:
        raise UnitProvenanceError(
            f"City {city.name!r}: member_maxes is empty — cannot validate unit"
        )
    if not np.isfinite(arr).all():
        raise UnitProvenanceError(
            f"City {city.name!r}: member_maxes contains non-finite values"
        )

    median = float(np.median(arr))
    if city.settlement_unit == "F":
        lo, hi = _F_PLAUSIBLE_RANGE
    elif city.settlement_unit == "C":
        lo, hi = _C_PLAUSIBLE_RANGE
    else:
        raise UnitProvenanceError(
            f"City {city.name!r} has unknown settlement_unit "
            f"{city.settlement_unit!r}"
        )

    if not (lo <= median <= hi):
        raise UnitProvenanceError(
            f"City {city.name!r} expects unit={city.settlement_unit!r}, but "
            f"member median={median:.2f} is outside plausible range "
            f"[{lo}, {hi}]. This likely indicates cross-unit contamination "
            f"in ensemble_snapshots.members_json for this snapshot."
        )


def validate_members_vs_observation(
    member_maxes: np.ndarray,
    city: City,
    observed_value: float,
) -> None:
    """Second-line unit-provenance check anchored to the VERIFIED observation.

    Closes the asymmetric gap in ``validate_members_unit_plausible``: °C
    daily-max values (0-30 °C) fall inside the F plausible range [-50, 140],
    so a °C-in-°F leak would pass the univariate median check silently.
    This function anchors the plausibility test to the observation for
    the same (city, target_date) and uses a relative tolerance scaled by
    the ensemble spread:

        tol = min(cap, max(floor, k * (max(members) - min(members))))

    where (per ``city.settlement_unit``) ``floor=25/40``, ``cap=28/50``,
    and ``k=_VS_OBS_SPREAD_MULTIPLIER`` (4). Rationale lives at the
    module-level constants ``_C_VS_OBS_MAX_OFFSET_*``; see the comment
    block there for the Helsinki/contamination calibration that picked
    these numbers.

    Rationale for layering two checks:
        - ``validate_members_unit_plausible`` catches Kelvin leaks and
          far-out-of-range values without needing an observation.
        - ``validate_members_vs_observation`` catches °C-in-°F (and
          vice-versa) by using the observation as the unit anchor; the
          relative-tolerance form admits real arctic misses while the
          absolute cap still rejects wide-spread cross-unit values.
        - Both run per-snapshot in the rebuild script; a snapshot must
          pass both or it is unit-rejected.
    """
    arr = np.asarray(member_maxes, dtype=float)
    if arr.size == 0:
        raise UnitProvenanceError(
            f"City {city.name!r}: member_maxes is empty — cannot anchor "
            f"to observation"
        )
    if not np.isfinite(arr).all():
        raise UnitProvenanceError(
            f"City {city.name!r}: member_maxes contains non-finite values"
        )
    if not np.isfinite(observed_value):
        raise UnitProvenanceError(
            f"City {city.name!r}: observed_value={observed_value!r} is "
            f"non-finite — cannot anchor unit check"
        )

    median = float(np.median(arr))
    offset = abs(median - float(observed_value))
    spread = float(arr.max() - arr.min()) if arr.size > 1 else 0.0
    if city.settlement_unit == "F":
        floor = _F_VS_OBS_MAX_OFFSET_FLOOR
        cap = _F_VS_OBS_MAX_OFFSET_CAP
    elif city.settlement_unit == "C":
        floor = _C_VS_OBS_MAX_OFFSET_FLOOR
        cap = _C_VS_OBS_MAX_OFFSET_CAP
    else:
        raise UnitProvenanceError(
            f"City {city.name!r} has unknown settlement_unit "
            f"{city.settlement_unit!r}"
        )
    tol = min(cap, max(floor, _VS_OBS_SPREAD_MULTIPLIER * spread))

    if offset > tol:
        raise UnitProvenanceError(
            f"City {city.name!r}: member median={median:.2f} vs observation="
            f"{observed_value:.2f} offset={offset:.2f} {city.settlement_unit} "
            f"exceeds tolerance {tol:.1f} (floor={floor:.1f}, "
            f"spread={spread:.2f}, k={_VS_OBS_SPREAD_MULTIPLIER}, "
            f"cap={cap:.1f}). "
            f"This is far outside any plausible "
            f"TIGGE forecast error at 24h-7d lead and strongly suggests "
            f"cross-unit contamination between members_json and the "
            f"observation's unit."
        )
