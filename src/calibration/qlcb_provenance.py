# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K3 (q_lcb settlement-backward-coverage). Plan R1 made
#   structural. K3 root: q_lcb was never settlement-grounded -> the live LCB ran
#   ~26pt overconfident, and at the lcb_by_direction seam a bare float erased WHERE
#   the LCB came from (forecast bootstrap vs EMOS analytic vs settlement isotonic),
#   so the coverage gate could not reason about whether it was allowed to fire.
#   Antibody discipline (Fitz #4 + immune-system): make "a q_lcb with no provenance
#   reached the consumer" a TypeError at __setitem__, not a runtime patch. The error
#   CATEGORY is unconstructable, not the instance.
"""QlcbProvenance — a q_lcb that carries WHERE its calibration came from.

The live q-LCB (``lcb_by_direction`` in event_reactor_adapter) crosses a boundary
from the PRODUCER (the family bootstrap / EMOS analytic CI) to the CONSUMER
(robust trade_score, evaluate_kelly, the ARM coverage gate). Today the carrier is
``dict[tuple, float]`` — the float erases the calibration source, so:

  * the settlement-coverage gate cannot tell a forecast-bootstrap LCB (which it
    may re-calibrate against the settled record) from an already-settlement-
    isotonic LCB (which it must not double-shrink); and
  * an un-provenanced number can silently reach trade_score as if it were honest.

``QlcbProvenance`` makes the source a TYPE field, and ``QlcbByDirection`` (a dict
subclass) refuses any bare-float assignment. The float is still readable through
``.q_lcb`` so every existing consumer keeps working — but only via the carrier.

Public API:
  QlcbProvenance      — frozen (q_lcb, calibration_source, n_settlement_observations,
                        coverage_ratio).
  QlcbByDirection     — dict[(condition_id, direction) -> QlcbProvenance] that raises
                        TypeError on bare-float __setitem__ / update.
  CALIBRATION_SOURCES — the closed vocabulary of calibration sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# The closed vocabulary. A q_lcb is born from exactly one of these:
#   FORECAST_BOOTSTRAP  — the family hypothesis-scan percentile CI (today's MC path).
#   EMOS_ANALYTIC       — the coverage-honest EMOS analytic CI (the licensed override).
#   SETTLEMENT_ISOTONIC — re-grounded against the realized settlement win-rate (K3).
CalibrationSource = Literal["FORECAST_BOOTSTRAP", "EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC"]
CALIBRATION_SOURCES: frozenset[str] = frozenset(
    {"FORECAST_BOOTSTRAP", "EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC"}
)


@dataclass(frozen=True)
class QlcbProvenance:
    """A q-LCB value plus the provenance of its calibration.

    Frozen: once the source is recorded the band cannot be silently re-written.
    The probability lower bound lives in ``q_lcb`` (read it via ``.q_lcb``); the
    calibration source and (when settlement-grounded) the settled-observation
    count + coverage ratio travel WITH the number across every boundary.
    """

    q_lcb: float
    calibration_source: CalibrationSource
    n_settlement_observations: Optional[int] = None
    coverage_ratio: Optional[float] = None

    def __post_init__(self) -> None:
        if self.calibration_source not in CALIBRATION_SOURCES:
            raise ValueError(
                f"QlcbProvenance.calibration_source={self.calibration_source!r} is "
                f"not in the closed vocabulary {sorted(CALIBRATION_SOURCES)!r}. An "
                f"un-vocabularied source cannot reach the coverage gate."
            )
        try:
            q = float(self.q_lcb)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"QlcbProvenance.q_lcb must be a real number: {exc}") from exc
        # q_lcb is a probability lower bound. Out-of-[0,1] is a domain error, not a
        # value to silently clamp (a clamp would hide an upstream sign/scale bug).
        if not (0.0 <= q <= 1.0):
            raise ValueError(
                f"QlcbProvenance.q_lcb={q!r} is outside [0, 1]; a probability lower "
                f"bound cannot live there."
            )


class QlcbByDirection(dict):
    """``dict[(condition_id, direction) -> QlcbProvenance]`` with a typed write door.

    THE antibody: a bare-float assignment is a ``TypeError`` at the boundary, so an
    un-provenanced q-LCB is unconstructable in the live carrier. Both ``d[k] = v``
    and ``d.update(...)`` funnel through the same guard — there is no back door.

    Reads are plain dict reads (the value is a ``QlcbProvenance``); a consumer that
    only wants the number reads ``d[k].q_lcb``.
    """

    def __setitem__(self, key, value) -> None:  # noqa: ANN001 - dict signature
        if not isinstance(value, QlcbProvenance):
            raise TypeError(
                f"QlcbByDirection[{key!r}] must be a QlcbProvenance, got "
                f"{type(value).__name__}. A bare q_lcb float with no calibration "
                f"provenance cannot cross this boundary — wrap it: "
                f"QlcbProvenance(q_lcb=..., calibration_source=...)."
            )
        super().__setitem__(key, value)

    def update(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        # Route every incoming pair through __setitem__ so the guard cannot be
        # bypassed via update(). Mirrors dict.update's mapping/iterable contract.
        if args:
            if len(args) > 1:
                raise TypeError(
                    f"update expected at most 1 positional argument, got {len(args)}"
                )
            other = args[0]
            if hasattr(other, "keys"):
                for k in other.keys():
                    self[k] = other[k]
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):  # noqa: ANN001
        if key not in self:
            # default must also satisfy the type guard.
            self[key] = default
        return self[key]


# ---------------------------------------------------------------------------
# Carrier helpers — let the override / day0-mask / coverage logic run ONCE over
# both the live typed carrier (QlcbByDirection) AND the legacy plain-float dicts
# the existing EMOS-CI unit tests pass. Carrier-driven: a QlcbByDirection gets a
# typed entry; a plain dict gets a bare float. Reads are polymorphic.
# ---------------------------------------------------------------------------
def _qlcb_float(value) -> float:  # noqa: ANN001
    """Read the float out of a q_lcb carrier entry.

    Polymorphic: accepts a ``QlcbProvenance`` (returns ``.q_lcb``) OR a bare float
    (returns it). This is the single read door every consumer uses so the type can
    sit at the boundary without forcing a branch at each of the ~6 read sites.
    """
    if isinstance(value, QlcbProvenance):
        return float(value.q_lcb)
    return float(value)


def _set_qlcb_provenance(
    carrier,  # noqa: ANN001
    key,  # noqa: ANN001
    q_lcb: float,
    *,
    source: CalibrationSource,
    n_settlement_observations: Optional[int] = None,
    coverage_ratio: Optional[float] = None,
) -> None:
    """Write a q_lcb into ``carrier`` carrying its calibration ``source``.

    On a ``QlcbByDirection`` the entry is a typed ``QlcbProvenance`` (the live
    boundary). On a PLAIN dict the entry is a bare float — preserving the existing
    EMOS-CI override tests that assert plain-float equality. The provenance is
    therefore enforced exactly where the live carrier demands it, and the same
    override/mask/coverage code drives both.
    """
    if isinstance(carrier, QlcbByDirection):
        carrier[key] = QlcbProvenance(
            q_lcb=float(q_lcb),
            calibration_source=source,
            n_settlement_observations=n_settlement_observations,
            coverage_ratio=coverage_ratio,
        )
    else:
        carrier[key] = float(q_lcb)
