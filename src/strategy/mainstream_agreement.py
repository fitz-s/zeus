# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: Task #135 mainstream-forecast direction-agreement gate
#   (/tmp/arm-truth.md keystone finding); DIRECTION LAW
#   (feedback_buy_direction_semantic); operator ARM criterion
#   (project_live_goal_2026_06_03). trade_score formula at
#   src/strategy/live_inference/trade_score.py:48-52. Bin type at
#   src/types/market.py.
"""Mainstream-forecast direction-agreement gate (#135).

REFERENCE-ONLY (operator directive 2026-06-03). This module computes an
independent cross-check: does OUR forecast AGREE with an external mainstream
forecast, and is the traded direction consistent with both the mainstream-implied
bin and our own modal bin? The verdict is RECORDED on every shadow receipt to
inform the ARM decision — it lets the operator see, on the forecast's top
candidate, whether internal and external signals independently agree.

It takes NO part in production selection. Production trades on the FORECAST
(trade_score / q_lcb / Kelly); the gate verdict can NEVER exclude a candidate
(see event_reactor_adapter._selected_candidate_proof). Operator rationale: "if we
use the forecast for calculation, none of these exist — we just use the forecast
to trade; the only reason we are in shadow is the candidates do not yet reflect
real Polymarket trades, not this gate." The real fix for cold/warm-bias false
positives is the FORECAST (the bias correction itself), not a blocking gate.

The four checks below still compute a meaningful pass/fail SIGNAL (the ARM
reference); the #135-B independence flag records when a mainstream agreement was
manufactured by a large bias correction. None of it filters the trade.

DIRECTION LAW (never invert — operator-flagged recurring confusion):
  - buy_yes(bin) ⟺ traded bin ≈ forecast modal bin (we predict it SETTLES here).
  - buy_no(bin)  ⟺ traded bin ≠ forecast modal bin (we predict it does NOT).

The four checks (ALL must hold; the verdict records each + the deltas):
  1. mainstream_available — a fresh independent mainstream point exists for
     (city, target_date). Missing/stale ⇒ FAIL_CLOSED (never auto-pass).
  2. mainstream_close — |our_point − mainstream_point| ≤ tolerance
     (1.5°C for °C cities, 2°F for °F cities). Diverging beyond tolerance is the
     cold/warm-bias false-positive signature ⇒ FAIL. (Kills SF / Tel Aviv.)
  3. direction_agrees_mainstream — tolerance-aware check against the traded bin:
     buy_yes ⟺ mainstream point is within ±tolerance of the traded bin (mainstream
     broadly agrees the traded bin is the likely outcome — no rounding knife-edge);
     buy_no ⟺ mainstream point is NOT within ±tolerance of the traded bin (we're
     correctly shorting a bin mainstream says won't settle, e.g. Panama ≥31 when
     mainstream=28). Boundary: X.49 and X.50 classify identically.
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
# RETIRED as a fail path (2026-06-03). Originally demoted candidates whose
# mainstream agreement existed only because a large bias correction moved the
# forecast (raw disagreed). The grid-to-point investigation proved those
# corrections are OOS-validated legitimate (raw is the biased number), so the
# condition is now recorded as pure provenance (verdict.agreement_correction_dependent)
# and never sets fail_reason. Constant kept for receipt-vocab back-compat.
FAIL_BIAS_CORRECTION_DEPENDENT = "AGREEMENT_BIAS_CORRECTION_DEPENDENT"
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
    # Provenance fields (#135-B, demotion retired 2026-06-03). raw_our_point is the
    # UNCORRECTED ensemble mean. agreement_correction_dependent records when the
    # mainstream agreement relies on the bias correction (corrected close, raw not).
    # These are INFORMATIONAL only — the operator reads the raw-vs-corrected
    # divergence; they never flip `passed` (the correction is OOS-validated
    # legitimate; we trade on the corrected forecast).
    raw_our_point: float | None = None
    bias_applied: float | None = None          # our_point - raw_our_point
    agrees_on_raw: bool | None = None           # |raw_our_point - mainstream| <= tol
    agreement_correction_dependent: bool = False

    def to_dict(self) -> dict:
        """Flat dict for the no_submit receipt tag (`mainstream_agreement_*`).

        Key `mainstream_delta` is the canonical receipt/DB field name (matching
        the EventSubmissionReceipt and edli_no_submit_receipts column names).
        Key `forecast_delta` is kept for back-compat with existing callers and
        shadow receipt analysis scripts.
        """
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
            "forecast_delta": self.forecast_delta,   # back-compat alias
            "mainstream_delta": self.forecast_delta,  # canonical receipt/DB field name
            "tolerance": self.tolerance,
            "traded_bin_label": self.traded_bin_label,
            "direction": self.direction,
            "raw_our_point": self.raw_our_point,
            "bias_applied": self.bias_applied,
            "agrees_on_raw": self.agrees_on_raw,
            "agreement_correction_dependent": self.agreement_correction_dependent,
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


def mainstream_within_tolerance_of_bin(
    mainstream_point: float, bin_: Bin, tolerance: float
) -> bool:
    """True if mainstream_point is within ±tolerance of bin_.

    "Within tolerance of a bin" means the mainstream point lies in the interval
    [bin.low - tolerance, bin.high + tolerance], where None boundaries are open
    (−∞ / +∞).  This is the correct check-3 predicate — using single-rounded-bin
    equality is rounding-brittle: mainstream=15.8 rounds to 16, but is genuinely
    close to the 15°C traded bin (Δ=0.8 < 1.5°C tolerance).

    For buy_yes: PASS when mainstream is within tolerance of traded bin (i.e. the
    mainstream broadly agrees the traded bin is the likely outcome).
    For buy_no: FAIL when mainstream is within tolerance of traded bin (i.e. we'd
    be shorting a bin mainstream says may actually settle).

    Boundary: X.49 and X.50 classify identically — there is no rounding knife-edge.
    """
    pt = float(mainstream_point)
    low_ok = bin_.low is None or pt >= float(bin_.low) - tolerance
    high_ok = bin_.high is None or pt <= float(bin_.high) + tolerance
    return low_ok and high_ok


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
    raw_our_point: float | None = None,
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

    # CHECK 2b — PROVENANCE ANNOTATION (informational; does NOT demote as of
    # 2026-06-03). our_point is the bias-CORRECTED forecast; raw_our_point is the
    # UNCORRECTED ensemble mean. agreement_correction_dependent records WHEN the
    # corrected forecast is close to mainstream but the raw is not — i.e. the
    # mainstream agreement relies on the bias correction. This was originally a
    # demotion (#135-B), on the hypothesis the +4° cold-bias correction was
    # "manufactured". The 2026-06-03 grid-to-point investigation DISPROVED that:
    # the correction is OOS-validated legitimate (the raw is the biased number, the
    # corrected is the better estimate — ECMWF mx2t3 is a grid-cell average, coastal
    # cells average in cool sea while the settling station is warm/inland). So the
    # flag is now PURE PROVENANCE the operator can read (raw vs corrected divergence),
    # NOT a verdict — we trade on the corrected forecast. When raw_our_point is
    # absent the annotation is simply skipped.
    raw_pt = None if raw_our_point is None else float(raw_our_point)
    bias_applied = None if raw_pt is None else (float(our_point) - raw_pt)
    agrees_on_raw = None if raw_pt is None else (abs(raw_pt - main_pt) <= tol)
    agreement_correction_dependent = bool(mainstream_close and agrees_on_raw is False)

    # CHECK 3 — direction agrees with the mainstream-implied bin (DIRECTION LAW).
    #
    # Tolerance-aware (fixes rounding-brittleness): mainstream 15.8°C rounds to
    # 16°C, but the genuine Wellington 15°C buy_yes candidate (Δ=0.8°C) must pass
    # check-3. Hard bin-equality fails it at the knife-edge; tolerance-aware passes it.
    #
    # Semantics (DIRECTION LAW, tolerance-aware):
    #   buy_yes ⟺ mainstream point is within ±tolerance of traded bin
    #             (mainstream broadly implies the traded bin is the likely outcome).
    #   buy_no  ⟺ mainstream point is NOT within ±tolerance of traded bin
    #             (mainstream says the traded bin is unlikely → safe to short it).
    #
    # Boundary: X.49 and X.50 classify identically (no rounding knife-edge).
    mainstream_near_traded_bin = mainstream_within_tolerance_of_bin(main_pt, traded_bin, tol)
    if direction == "buy_yes":
        direction_agrees_mainstream = mainstream_near_traded_bin
    else:  # buy_no
        direction_agrees_mainstream = not mainstream_near_traded_bin

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
    # passed reflects whether the forecast WE TRADE (the bias-corrected point)
    # agrees with mainstream and is direction-consistent. agreement_correction_dependent
    # is recorded but does NOT demote (see CHECK 2b — the correction is legitimate;
    # demoting a city for carrying a validated correction would penalise the correct
    # forecast). Reference-only either way: this verdict never gates production.
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
        raw_our_point=raw_pt,
        bias_applied=bias_applied,
        agrees_on_raw=agrees_on_raw,
        agreement_correction_dependent=agreement_correction_dependent,
    )
