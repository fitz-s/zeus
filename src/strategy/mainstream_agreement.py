# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: Task #135 mainstream-forecast direction-agreement gate
#   (/tmp/arm-truth.md keystone finding); DIRECTION LAW
#   (feedback_buy_direction_semantic); operator ARM criterion
#   (project_live_goal_2026_06_03). trade_score formula at
#   src/strategy/live_inference/trade_score.py:48-52. Bin type at
#   src/types/market.py.
"""Mainstream-forecast direction-agreement gate (#135).

THE STRUCTURAL ANTIBODY. The EDLI reactor ranks candidates by `trade_score`,
which is *maximized* by the cold/warm-bias failure mode: the worse OUR forecast
diverges from reality, the higher the false q (and trade_score) on a wrong-side
bet. With no independent reference, trade_score rank is anti-correlated with
arm-eligibility for the bias cities (SF, Tel Aviv). This module makes that
false-positive CATEGORY impossible — not by patching today's instance, but by
requiring every arm/trade-eligible candidate to AGREE with an independent
mainstream forecast AND be direction-consistent both with the mainstream and
with our own modal bin.

The gate is a STANDARD, not a re-weight: a high enough trade_score can never buy
back eligibility once any of the four checks fails.

DIRECTION LAW (never invert — operator-flagged recurring confusion):
  - buy_yes(bin) ⟺ traded bin ≈ forecast modal bin (we predict it SETTLES here).
  - buy_no(bin)  ⟺ traded bin ≠ forecast modal bin (we predict it does NOT).

The four checks (ALL must hold; the verdict records each + the deltas):
  1. mainstream_available — a fresh independent mainstream point exists for
     (city, target_date). Missing/stale ⇒ FAIL_CLOSED (never auto-pass).
  2. mainstream_close — |our_point − mainstream_point| ≤ tolerance
     (1.5°C for °C cities, 2°F for °F cities). Diverging beyond tolerance is the
     cold/warm-bias false-positive signature ⇒ FAIL. (Kills SF / Tel Aviv.)
  3. direction_agrees_mainstream — derive the mainstream-implied bin (the family
     bin that CONTAINS the mainstream point); buy_yes requires the traded bin to
     BE that bin (the likely outcome); buy_no requires the traded bin to be a
     DIFFERENT bin (we're correctly shorting a bin mainstream says won't settle,
     e.g. Panama ≥31 when mainstream=28).
  4. direction_agrees_our_modal — buy_yes ⟹ traded bin == our modal bin;
     buy_no ⟹ traded bin ≠ our modal bin. Catches the direction inversion vs our
     OWN forecast (Tel Aviv 06-03 buy_yes on 25°C while our modal is 26°C).

This module is PURE — no I/O, no DB, no network. The independent mainstream point
is supplied by the caller (see src/data/mainstream_forecast_source.py). Keeping
the gate pure makes its invariant unit-testable as a cross-module relationship.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from src.contracts.settlement_semantics import round_wmo_half_up_value
from src.types.market import Bin

# Tolerances. °F bins span 2 settled degrees; °C bins span 1. A forecast within
# ~one bin-width of the mainstream is "close"; beyond that the divergence is the
# bias signature, not noise.
TOLERANCE_F: float = 2.0
TOLERANCE_C: float = 1.5

VALID_DIRECTIONS = ("buy_yes", "buy_no")

# Fail-reason taxonomy (recorded on the receipt tag so demotions are auditable).
FAIL_MAINSTREAM_CLOSED = "MAINSTREAM_FAIL_CLOSED"
FAIL_NOT_CLOSE = "MAINSTREAM_NOT_CLOSE"
FAIL_DIR_VS_MAINSTREAM = "DIRECTION_AGREES_MAINSTREAM_SHORTING_LIKELY"
FAIL_DIR_VS_MAINSTREAM_YES = "DIRECTION_DISAGREES_MAINSTREAM_BUY_YES_OFF_BIN"
FAIL_DIR_VS_OUR_MODAL = "DIRECTION_INVERSION_VS_OUR_MODAL"
PASS = "PASS"


@dataclass(frozen=True)
class MainstreamAgreementVerdict:
    """The per-candidate gate result. Every field is recorded on the shadow
    receipt so a demotion/exclusion is fully auditable (Fitz provenance rule)."""

    city: str
    target_date: str
    unit: str
    direction: str
    traded_bin_label: str
    our_point: float
    our_modal_bin_label: str | None
    mainstream_point: float | None
    mainstream_bin_label: str | None
    forecast_delta: float | None  # signed: our_point - mainstream_point
    tolerance: float
    mainstream_available: bool
    mainstream_close: bool
    direction_agrees_mainstream: bool
    direction_agrees_our_modal: bool
    passed: bool
    fail_reason: str

    def to_dict(self) -> dict:
        """Flat dict for the no_submit receipt tag (`mainstream_agreement_*`)."""
        return {
            "mainstream_agreement_pass": self.passed,
            "mainstream_agreement_fail_reason": self.fail_reason,
            "fail_reason": self.fail_reason,
            "mainstream_available": self.mainstream_available,
            "mainstream_close": self.mainstream_close,
            "direction_agrees_mainstream": self.direction_agrees_mainstream,
            "direction_agrees_our_modal": self.direction_agrees_our_modal,
            "our_point": self.our_point,
            "our_modal_bin_label": self.our_modal_bin_label,
            "mainstream_point": self.mainstream_point,
            "mainstream_bin_label": self.mainstream_bin_label,
            "forecast_delta": self.forecast_delta,
            "tolerance": self.tolerance,
            "traded_bin_label": self.traded_bin_label,
            "direction": self.direction,
        }


def tolerance_for_unit(unit: str) -> float:
    u = (unit or "").strip().upper()
    if u == "F":
        return TOLERANCE_F
    if u == "C":
        return TOLERANCE_C
    raise ValueError(f"mainstream-agreement tolerance: unit must be 'F' or 'C', got {unit!r}")


def bin_containing(
    point: float | None, bins: Sequence[Bin], *, precision: float = 1.0
) -> Bin | None:
    """Return the family bin that contains the point's settled value, or None.

    CROSS-MODULE INVARIANT: a continuous forecast point must be rounded to its
    integer settlement value (WMO half-up, floor(x+0.5)) BEFORE bin lookup — the
    SAME rounding the q-computation applies (market_analysis._settle →
    apply_settlement_rounding). Without this, a 25.4°C point would land in NO
    °C point bin (which contains only the exact integer 25), silently diverging
    the gate's bin from the q-computation's bin.
    """
    if point is None:
        return None
    settled = round_wmo_half_up_value(float(point), precision)
    for b in bins:
        if b.contains(settled):
            return b
    return None


def modal_bin_from_members(
    members: Sequence[float] | None, bins: Sequence[Bin], *, precision: float = 1.0
) -> Bin | None:
    """Our forecast modal bin = the family bin holding the most ensemble members.

    This is OUR model's belief — the direction-law reference for check (4). Each
    member is settlement-rounded (WMO half-up) before binning, matching the
    q-computation's member binning. Ties break toward the lower bin
    (deterministic). Returns None if no members or none land in any bin.
    """
    if not members:
        return None
    counts: list[int] = [0] * len(bins)
    for m in members:
        settled = round_wmo_half_up_value(float(m), precision)
        for i, b in enumerate(bins):
            if b.contains(settled):
                counts[i] += 1
                break
    best_i = -1
    best_n = 0
    for i, n in enumerate(counts):
        if n > best_n:
            best_n = n
            best_i = i
    if best_i < 0:
        return None
    return bins[best_i]


def _same_bin(a: Bin | None, b: Bin | None) -> bool:
    """Identity by (low, high, unit) — label-independent so cosmetic label
    differences never silently flip a direction verdict."""
    if a is None or b is None:
        return False
    return (a.low, a.high, a.unit) == (b.low, b.high, b.unit)


def evaluate_mainstream_agreement(
    *,
    city: str,
    target_date: str,
    unit: str,
    our_point: float,
    bins: Sequence[Bin],
    traded_bin: Bin,
    direction: str,
    members: Sequence[float] | None,
    mainstream_point: float | None,
    precision: float = 1.0,
) -> MainstreamAgreementVerdict:
    """Evaluate the four-check mainstream-agreement gate for one candidate.

    Returns a fully-populated verdict. `passed` is True only when all four
    checks hold. FAIL-CLOSED on missing mainstream — never auto-pass.

    `precision` is the market's settlement precision (1.0 = whole degrees); it
    governs the WMO half-up rounding used to map continuous points/members to
    settlement bins, matching the q-computation.
    """
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}")

    tol = tolerance_for_unit(unit)
    our_modal = modal_bin_from_members(members, bins, precision=precision)
    our_modal_label = our_modal.label if our_modal is not None else None

    # CHECK 1 — mainstream availability (FAIL-CLOSED).
    mainstream_available = mainstream_point is not None
    if not mainstream_available:
        return MainstreamAgreementVerdict(
            city=city,
            target_date=target_date,
            unit=unit,
            direction=direction,
            traded_bin_label=traded_bin.label,
            our_point=float(our_point),
            our_modal_bin_label=our_modal_label,
            mainstream_point=None,
            mainstream_bin_label=None,
            forecast_delta=None,
            tolerance=tol,
            mainstream_available=False,
            mainstream_close=False,
            direction_agrees_mainstream=False,
            direction_agrees_our_modal=False,
            passed=False,
            fail_reason=FAIL_MAINSTREAM_CLOSED,
        )

    main_pt = float(mainstream_point)
    forecast_delta = float(our_point) - main_pt
    mainstream_bin = bin_containing(main_pt, bins, precision=precision)
    mainstream_bin_label = mainstream_bin.label if mainstream_bin is not None else None

    # CHECK 2 — closeness (cold/warm-bias kill switch).
    mainstream_close = abs(forecast_delta) <= tol

    # CHECK 3 — direction agrees with the mainstream-implied bin (DIRECTION LAW).
    #   buy_yes ⟺ traded bin IS the mainstream bin (we predict the likely bin).
    #   buy_no  ⟺ traded bin is a DIFFERENT bin (we short an unlikely bin).
    traded_is_mainstream_bin = _same_bin(traded_bin, mainstream_bin)
    if direction == "buy_yes":
        direction_agrees_mainstream = traded_is_mainstream_bin
    else:  # buy_no
        direction_agrees_mainstream = not traded_is_mainstream_bin

    # CHECK 4 — direction consistent with OUR OWN modal bin (inversion catch).
    #   buy_yes ⟺ traded bin == our modal bin.
    #   buy_no  ⟺ traded bin != our modal bin.
    traded_is_our_modal_bin = _same_bin(traded_bin, our_modal)
    if direction == "buy_yes":
        direction_agrees_our_modal = traded_is_our_modal_bin
    else:  # buy_no
        direction_agrees_our_modal = not traded_is_our_modal_bin

    # Verdict aggregation with a deterministic, ordered fail-reason. Priority is
    # by epistemic grounding: the externally-grounded failures first, the
    # internal-consistency catch last.
    #   1. closeness  — the dominant cold/warm-bias kill (external reference).
    #   2. mainstream-direction — shorting/longing against the external bin.
    #   3. our-modal inversion — internal consistency vs our own forecast; this
    #      is the catch that fires when our forecast AGREES with mainstream (so
    #      checks 1-2 pass) yet the assigned direction still contradicts our own
    #      modal bin (the bias-warped-q wrong-side firing the operator flagged).
    passed = (
        mainstream_close
        and direction_agrees_mainstream
        and direction_agrees_our_modal
    )
    if passed:
        fail_reason = PASS
    elif not mainstream_close:
        fail_reason = FAIL_NOT_CLOSE
    elif not direction_agrees_mainstream:
        fail_reason = (
            FAIL_DIR_VS_MAINSTREAM if direction == "buy_no" else FAIL_DIR_VS_MAINSTREAM_YES
        )
    elif not direction_agrees_our_modal:
        fail_reason = FAIL_DIR_VS_OUR_MODAL
    else:  # pragma: no cover - defensive; covered by the boolean above
        fail_reason = PASS

    return MainstreamAgreementVerdict(
        city=city,
        target_date=target_date,
        unit=unit,
        direction=direction,
        traded_bin_label=traded_bin.label,
        our_point=float(our_point),
        our_modal_bin_label=our_modal_label,
        mainstream_point=main_pt,
        mainstream_bin_label=mainstream_bin_label,
        forecast_delta=forecast_delta,
        tolerance=tol,
        mainstream_available=True,
        mainstream_close=mainstream_close,
        direction_agrees_mainstream=direction_agrees_mainstream,
        direction_agrees_our_modal=direction_agrees_our_modal,
        passed=passed,
        fail_reason=fail_reason,
    )
