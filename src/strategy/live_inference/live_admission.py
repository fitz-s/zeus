"""Shared live-trade admission primitives.

These helpers express objective-level live-money constraints that are broader
than per-family ranking. They do not change q, price, FDR, Kelly, or venue state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

# BUY-NO MATERIAL-BIN VOCABULARY: this is stricter than the replacement certificate
# coverage predicate. A typed INSUFFICIENT_DATA verdict proves the coverage authority ran
# and is enough for the certificate bridge to continue into ordinary live gates, but it
# is not enough to waive the special material-bin buy-NO evidence requirement.
from src.calibration.settlement_backward_coverage import (
    settlement_coverage_refutes_claim,
)
from src.decision_kernel.canonicalization import stable_hash


# Compatibility default for callers that can optionally request a hit-rate
# constraint. The capital objective has no positive absolute probability floor:
# executable price, fees, robust edge, and robust delta-log wealth decide.
LIVE_DIRECTION_WIN_RATE_FLOOR = 0.0
LIVE_NEAR_SETTLED_ENTRY_PRICE_CEILING = 0.99
# Exact-bin YES and native NO share the same zero absolute floor. Their own
# current side-native executable cost supplies the economically meaningful bar.
LIVE_QKERNEL_CENTER_YES_MIN_Q_LCB = LIVE_DIRECTION_WIN_RATE_FLOOR
LIVE_QKERNEL_EXACT_YES_STRATEGY_KEYS = frozenset({
    "center_buy",
    "forecast_qkernel_entry",
})
LIVE_NEAR_DAY0_FORECAST_ENTRY_LEAD_HOURS = 12.0
LIVE_NEAR_DAY0_FORECAST_ENTRY_POST_START_HOURS = 24.0
LIVE_NEAR_DAY0_RAW_EXTREMA_MARGIN_NATIVE = 1.0

# A buy-NO on a single settlement bin is not a generic "not this exact value"
# lottery when the model itself assigns material YES mass to that bin. The
# production-safe proof is a NO-side conservative bound from an allowed native NO
# calibration source. FIX-4 (§2): the allow-list must be a subset of the q_lcb
# carrier vocabulary (CALIBRATION_SOURCES) so every admitted source is one the
# QlcbByDirection carrier can honestly provenance. YES_UCB_DERIVED was removed
# because it is not a CalibrationSource — a value the carrier cannot express must
# never gate a live buy-NO.
LIVE_BUY_NO_MATERIAL_YES_POSTERIOR = 0.20
LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES = frozenset({"EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC"})
REPLACEMENT_BOOTSTRAP_MIN_DRAWS = 100

# Settlement-backward coverage verdict statuses under which the buy-NO material-bin
# conservative-evidence gate admits a fused-bootstrap q_lcb. This is intentionally
# narrower than the certificate bridge's "typed verdict exists" rule:
#   LICENSED          = realized settled win-rate backs the claimed q_lcb within tolerance.
#   UNLICENSED        = the record refuted the raw claim and the K3 shrink to realized-1pp
#                       was the verdict's output — the (shrunk) q_lcb is settled-backed.
#   INSUFFICIENT_DATA = thin/absent claim history; not overconfidence proof, but
#                       also not a material-bin buy-NO waiver.
# None / UNEVALUATED carry no executable verdict and are not admitted by this gate.
# Category inversion this kills: a record-BACKED bootstrap q_lcb must not be rejected while
# a record-REFUTED (re-branded) one is accepted. The verdict, not the brand, is the evidence.
SETTLEMENT_COVERAGE_LICENSING_STATUSES = frozenset(
    {"LICENSED", "UNLICENSED"}
)


def live_win_rate_floor_rejection_reason(
    *,
    q_lcb: float | int | None,
    floor: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> str | None:
    """Validate qLCB and apply only an explicitly requested absolute floor."""

    try:
        q_value = float(q_lcb)
        floor_value = float(floor)
    except (TypeError, ValueError):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=missing:min={float(floor):.4f}"
    if not math.isfinite(q_value):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=nonfinite:min={floor_value:.4f}"
    if not math.isfinite(floor_value) or floor_value < 0.0 or floor_value >= 1.0:
        raise ValueError(f"live win-rate floor must be in [0, 1), got {floor!r}")
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_PROBABILITY_QUALITY:q_lcb={q_value:.4f}:range=[0,1]"
    if q_value < floor_value:
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb={q_value:.4f}:min={floor_value:.4f}"
    return None


def qkernel_center_yes_quality_floor() -> float:
    """Absolute qLCB floor; zero because executable economics own admission."""

    return float(LIVE_QKERNEL_CENTER_YES_MIN_Q_LCB)


def is_qkernel_exact_yes_strategy(strategy_key: object) -> bool:
    return str(strategy_key or "").strip() in LIVE_QKERNEL_EXACT_YES_STRATEGY_KEYS


def live_entry_probability_quality_rejection_reason(
    *,
    q_lcb: float | int | None,
    direction: object = None,
    strategy_key: object = None,
    selection_authority_applied: object = None,
    qkernel_execution_economics: object = None,
    floor: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> str | None:
    """Return the probability-quality blocker for a live entry candidate.

    YES and NO share one validity rule, not a hit-rate target. A low-q claim is
    admissible only when downstream current executable economics prove positive
    robust edge and delta-log wealth; a high q cannot compensate for overpaying.
    """

    direction_value = getattr(direction, "value", direction)
    direction_text = str(direction_value or "").strip().lower()
    direction_is_buy_yes = direction_text in {"buy_yes", "yes", "direction.yes"}
    strategy_text = str(strategy_key or "").strip()
    authority_text = str(selection_authority_applied or "").strip()
    is_qkernel_center_yes = (
        direction_is_buy_yes
        and is_qkernel_exact_yes_strategy(strategy_text)
        and authority_text == "qkernel_spine"
        and isinstance(qkernel_execution_economics, Mapping)
        and str(qkernel_execution_economics.get("source") or "").strip() == "qkernel_spine"
    )
    if is_qkernel_center_yes:
        reason = live_win_rate_floor_rejection_reason(
            q_lcb=q_lcb,
            floor=floor,
        )
        if reason is None:
            return None
        return reason.replace(
            "ADMISSION_WIN_RATE_FLOOR",
            "ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR",
            1,
        )
    return live_win_rate_floor_rejection_reason(q_lcb=q_lcb, floor=floor)


def near_day0_raw_extrema_consistency_rejection_reason(
    *,
    event_type: object,
    direction: object,
    target_bin_low: float | int | None,
    target_bin_high: float | int | None,
    raw_member_min: float | int | None,
    raw_member_max: float | int | None,
    raw_member_count: int | None,
    lead_hours_to_target_start: float | int | None,
    source_cycle_time: object = None,
    margin_native: float = LIVE_NEAR_DAY0_RAW_EXTREMA_MARGIN_NATIVE,
    pre_start_window_hours: float = LIVE_NEAR_DAY0_FORECAST_ENTRY_LEAD_HOURS,
    post_start_window_hours: float = LIVE_NEAR_DAY0_FORECAST_ENTRY_POST_START_HOURS,
) -> str | None:
    """Reject forecast-lane exact-bin YES entries that contradict near-Day0 raw extrema.

    The q-kernel may buy a YES point/range with conservative probability below a
    binary 51% floor, but close to the local target day the same raw-model extrema
    that feed the spine must still support the selected bin. A daily posterior tail
    widened by sigma cannot overrule a fresh short-horizon member envelope.

    This predicate is intentionally one-way: it only guards forecast-lane buy-YES
    entries. Day0 observation events have their own authority, and buy-NO receives
    support from the selected-side NO bound rather than from overlap with the YES bin.
    """

    event_text = str(event_type or "").strip()
    if event_text not in {"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"}:
        return None
    direction_value = getattr(direction, "value", direction)
    if str(direction_value or "").strip().lower() not in {"buy_yes", "yes", "direction.yes"}:
        return None
    try:
        lead_hours = float(lead_hours_to_target_start)
        pre_window = float(pre_start_window_hours)
        post_window = float(post_start_window_hours)
        margin = float(margin_native)
    except (TypeError, ValueError):
        return "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_MISSING:lead_hours=missing"
    if not all(math.isfinite(v) for v in (lead_hours, pre_window, post_window, margin)):
        return "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_MISSING:lead_hours=nonfinite"
    if pre_window <= 0.0 or post_window <= 0.0 or margin < 0.0:
        raise ValueError("near-Day0 raw-extrema windows and margin must be positive")
    if lead_hours > pre_window or lead_hours < -post_window:
        return None
    if lead_hours <= 0.0:
        return (
            "ADMISSION_DAY0_FORECAST_ENTRY_REQUIRES_OBSERVATION_LANE:"
            f"lead_hours={lead_hours:.3f}"
        )
    try:
        count = int(raw_member_count or 0)
        raw_min = float(raw_member_min)
        raw_max = float(raw_member_max)
    except (TypeError, ValueError):
        return (
            "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_MISSING:"
            f"lead_hours={lead_hours:.3f}"
        )
    if count <= 0 or not all(math.isfinite(v) for v in (raw_min, raw_max)):
        return (
            "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_MISSING:"
            f"lead_hours={lead_hours:.3f}"
        )
    if raw_min > raw_max:
        return (
            "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_INVALID:"
            f"raw_min={raw_min:.3f}:raw_max={raw_max:.3f}:count={count}"
        )
    bin_low = None if target_bin_low is None else float(target_bin_low)
    bin_high = None if target_bin_high is None else float(target_bin_high)
    if bin_low is not None and not math.isfinite(bin_low):
        bin_low = None
    if bin_high is not None and not math.isfinite(bin_high):
        bin_high = None
    if bin_low is None and bin_high is None:
        return "ADMISSION_NEAR_DAY0_RAW_EXTREMA_EVIDENCE_INVALID:bin_bounds=missing"

    below_selected_bin = bin_low is not None and raw_max < (bin_low - margin)
    above_selected_bin = bin_high is not None and raw_min > (bin_high + margin)
    if not (below_selected_bin or above_selected_bin):
        return None
    cycle_text = str(source_cycle_time or "").strip() or "missing"
    return (
        "ADMISSION_NEAR_DAY0_RAW_EXTREMA_CONTRADICTION:"
        f"lead_hours={lead_hours:.3f}:raw_min={raw_min:.3f}:raw_max={raw_max:.3f}:"
        f"bin_low={'open' if bin_low is None else f'{bin_low:.3f}'}:"
        f"bin_high={'open' if bin_high is None else f'{bin_high:.3f}'}:"
        f"margin={margin:.3f}:count={count}:cycle={cycle_text}"
    )


def live_lcb_consistency_rejection_reason(
    *,
    q_direction: float | int | None,
    q_lcb: float | int | None,
) -> str | None:
    """Reject impossible conservative bounds before any ranking or sizing."""

    try:
        q_value = float(q_direction)
        q_lcb_value = float(q_lcb)
    except (TypeError, ValueError):
        return "ADMISSION_LCB_CONSISTENCY:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, q_lcb_value)):
        return "ADMISSION_LCB_CONSISTENCY:inputs=nonfinite"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_LCB_CONSISTENCY:q_direction={q_value:.4f}:range=[0,1]"
    if q_lcb_value < 0.0 or q_lcb_value > 1.0:
        return f"ADMISSION_LCB_CONSISTENCY:q_lcb={q_lcb_value:.4f}:range=[0,1]"
    if q_lcb_value > q_value:
        return f"ADMISSION_LCB_CONSISTENCY:q_lcb={q_lcb_value:.6f}:q_direction={q_value:.6f}"
    return None


def live_capital_efficiency_rejection_reason(
    *,
    q_lcb: float | int | None,
    execution_price: float | int | None,
    trade_score: float | int | None,
) -> str | None:
    """Reject only structurally non-positive conservative EV.

    The rule is direction-agnostic: ``q_lcb`` is already in the candidate's win
    direction, and ``execution_price`` is the fee-adjusted cost per share. Low
    maximum payout ROI and low robust EV/$ are ranking/sizing inputs, not fixed
    live blockers.
    """

    try:
        q_value = float(q_lcb)
        price = float(execution_price)
        score = float(trade_score)
    except (TypeError, ValueError):
        return "ADMISSION_CAPITAL_EFFICIENCY:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, price, score)):
        return "ADMISSION_CAPITAL_EFFICIENCY:inputs=nonfinite"
    if price <= 0.0 or price >= 1.0:
        return f"ADMISSION_CAPITAL_EFFICIENCY:price={price:.4f}:range=(0,1)"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_CAPITAL_EFFICIENCY:q_lcb={q_value:.4f}:range=[0,1]"
    conservative_ev_per_dollar = (q_value - price) / price
    if conservative_ev_per_dollar <= 0.0:
        return (
            "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:"
            f"ev_per_dollar={conservative_ev_per_dollar:.6f}:q_lcb={q_value:.6f}:price={price:.6f}"
        )
    return None


def live_near_settled_entry_price_rejection_reason(
    *,
    execution_price: float | int | None,
    ceiling: float = LIVE_NEAR_SETTLED_ENTRY_PRICE_CEILING,
) -> str | None:
    """Reject entries whose cost is already effectively settled.

    A candidate with an entry price at or above the ceiling can still show a tiny
    positive conservative EV when q_lcb is exactly 1.0, but it is no longer an
    exploitable market. This is an economic participation gate, not a probability
    override: the observed hard fact may remain true while the entry is skipped.
    """

    if execution_price is None:
        return None
    try:
        price = float(execution_price)
        price_ceiling = float(ceiling)
    except (TypeError, ValueError):
        return "ADMISSION_NEAR_SETTLED_PRICE:inputs=missing"
    if not math.isfinite(price) or not math.isfinite(price_ceiling):
        return "ADMISSION_NEAR_SETTLED_PRICE:inputs=nonfinite"
    if price_ceiling <= 0.0 or price_ceiling >= 1.0:
        raise ValueError(
            f"near-settled entry price ceiling must be in (0, 1), got {ceiling!r}"
        )
    if price >= price_ceiling:
        return (
            "ADMISSION_NEAR_SETTLED_PRICE:"
            f"price={price:.6f}:ceiling={price_ceiling:.6f}"
        )
    return None


# FIX B (incident 0b5c305e26524042, 2026-06-10 Milan-24C;
# docs/evidence/2026_06_10_milan_24c_first_fill_rootcause.md): the K3 settlement
# coverage gate fail-OPENS on INSUFFICIENT_DATA, so a tail band with no settled
# history keeps its raw FORECAST_BOOTSTRAP q_lcb - exactly where the model is least
# proven. This guard is the fail-CLOSED dual, scoped to the longshot-disagreement
# case only: a cheap candidate (price < TAIL_PRICE_MAX) whose unlicensed q_lcb
# claims more than DISAGREEMENT_RATIO x the market price is rejected until its
# band carries a settlement-licensed calibration source. Near-center trades
# (price >= TAIL_PRICE_MAX) are untouched by construction. Rejection (not shrink):
# shrinking q_lcb to the market-implied probability yields conservative EV <= 0,
# which the capital-efficiency gate rejects anyway - an explicit reason is the
# same outcome with honest provenance.
COVERAGE_UNLICENSED_TAIL_PRICE_MAX = 0.05
COVERAGE_UNLICENSED_TAIL_DISAGREEMENT_RATIO = 2.0
# Subset of qlcb_provenance.CALIBRATION_SOURCES that is settlement-licensed
# (mirrors LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES; FORECAST_BOOTSTRAP is not).
COVERAGE_LICENSED_LCB_SOURCES = frozenset({"EMOS_ANALYTIC", "SETTLEMENT_ISOTONIC"})


def coverage_unlicensed_tail_rejection_reason(
    *,
    q_lcb: float | int | None,
    execution_price: float | int | None,
    q_lcb_calibration_source: str | None,
    tail_price_max: float = COVERAGE_UNLICENSED_TAIL_PRICE_MAX,
    disagreement_ratio: float = COVERAGE_UNLICENSED_TAIL_DISAGREEMENT_RATIO,
    licensed_sources: frozenset[str] = COVERAGE_LICENSED_LCB_SOURCES,
) -> str | None:
    """Reject an unlicensed longshot disagreement with the market (direction-agnostic).

    Fires iff ALL of: execution_price < tail_price_max (longshot pricing),
    q_lcb > disagreement_ratio x execution_price (material disagreement), and
    the q_lcb calibration source is not settlement-licensed. An unpriced
    candidate (price None) is not this guard's business - the quote-missing
    no-trade path owns it.
    """
    if execution_price is None:
        return None
    try:
        price = float(execution_price)
        q_value = float(q_lcb)
        ratio_floor = float(disagreement_ratio)
        tail_max = float(tail_price_max)
    except (TypeError, ValueError):
        return "COVERAGE_UNLICENSED_TAIL:inputs=missing"
    if not all(math.isfinite(v) for v in (price, q_value, ratio_floor, tail_max)):
        return "COVERAGE_UNLICENSED_TAIL:inputs=nonfinite"
    if price <= 0.0 or price >= tail_max:
        return None
    if q_value <= ratio_floor * price:
        return None
    source = str(q_lcb_calibration_source or "").strip()
    if source in licensed_sources:
        return None
    return (
        "COVERAGE_UNLICENSED_TAIL:"
        f"q_lcb={q_value:.6f}:price={price:.6f}:ratio={q_value / price:.2f}:"
        f"max_ratio={ratio_floor:.2f}:source={source or 'missing'}"
    )


def live_buy_no_conservative_evidence_rejection_reason(
    *,
    direction: str | None,
    q_direction: float | int | None,
    q_lcb: float | int | None,
    execution_price: float | int | None,
    q_lcb_calibration_source: str | None,
    same_bin_yes_posterior: float | int | None = None,
    settlement_coverage_status: str | None = None,
    replacement_no_bound_certificate: Mapping[str, object] | None = None,
    replacement_no_bound_expected: Mapping[str, object] | None = None,
    qkernel_execution_economics: Mapping[str, object] | None = None,
    probability_authority: str | None = None,
    posterior_id: int | str | None = None,
    condition_id: str | None = None,
    material_yes_posterior: float = LIVE_BUY_NO_MATERIAL_YES_POSTERIOR,
    allowed_lcb_sources: frozenset[str] = LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
) -> str | None:
    """Block material-bin buy-NO without evidence-backed conservative NO LCB.

    ``q_direction`` is the candidate-direction posterior. ``same_bin_yes_posterior``
    must be supplied from an independently materialized YES-bin probability; this
    guard must never infer YES from a NO candidate by complement arithmetic. The
    guard is deliberately one-way: it never creates a trade and it does not touch
    buy-YES.

    ``settlement_coverage_status`` is the family's settlement-backward coverage
    verdict. When the q_lcb source is not in the allow-list, this special buy-NO
    gate admits only LICENSED or UNLICENSED-after-shrink semantics. INSUFFICIENT_DATA
    is allowed at the certificate layer but is not a material-bin buy-NO waiver.

    The single-authority replacement path is different: it can carry an exact
    selected-leg certificate proving ``q_no = 1 - q_yes`` and
    ``lcb_no = 1 - ucb_yes`` from one posterior.  That is native conservative NO
    evidence, not a source-name waiver.  Every scalar is rechecked here; an
    authority string or partial certificate alone never admits.
    """

    if direction != "buy_no":
        return None
    if same_bin_yes_posterior is None:
        return "ADMISSION_BUY_NO_INDEPENDENT_YES_POSTERIOR_MISSING"
    try:
        q_value = float(q_direction)
        q_lcb_value = float(q_lcb)
        price = float(execution_price)
        material_floor = float(material_yes_posterior)
        yes_posterior = float(same_bin_yes_posterior)
    except (TypeError, ValueError):
        return "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:inputs=missing"
    if not all(math.isfinite(v) for v in (q_value, q_lcb_value, price, material_floor, yes_posterior)):
        return "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:inputs=nonfinite"
    if q_value < 0.0 or q_value > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:q_direction={q_value:.4f}:range=[0,1]"
    if q_lcb_value < 0.0 or q_lcb_value > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:q_lcb={q_lcb_value:.4f}:range=[0,1]"
    if price <= 0.0 or price >= 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:price={price:.4f}:range=(0,1)"
    if yes_posterior < 0.0 or yes_posterior > 1.0:
        return f"ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE:yes_posterior={yes_posterior:.4f}:range=[0,1]"
    if material_floor <= 0.0 or material_floor >= 1.0:
        raise ValueError("buy-NO material-bin posterior floor must be in (0, 1)")

    replacement_authority = str(probability_authority or "").strip() == "replacement_0_1"
    if replacement_no_bound_certificate_matches(
        replacement_no_bound_certificate,
        expected=replacement_no_bound_expected,
        q_direction=q_value,
        q_lcb=q_lcb_value,
        same_bin_yes_posterior=yes_posterior,
        qkernel_execution_economics=qkernel_execution_economics,
        probability_authority=probability_authority,
        posterior_id=posterior_id,
        condition_id=condition_id,
    ):
        return None
    if replacement_authority:
        return (
            "ADMISSION_BUY_NO_REPLACEMENT_BOUND_CERTIFICATE_MISSING:"
            f"yes_posterior={yes_posterior:.6f}:no_q={q_value:.6f}:"
            f"no_q_lcb={q_lcb_value:.6f}"
        )

    if yes_posterior >= material_floor:
        source = str(q_lcb_calibration_source or "").strip()
        if source not in allowed_lcb_sources:
            status = str(settlement_coverage_status or "").strip()
            if status == "LICENSED" or settlement_coverage_refutes_claim(status):
                return None
            # FIX-4 (§2): the conservative_edge>confidence_gap waiver is DELETED.
            # It admitted a material-YES buy_no on a self-referential test of the
            # SAME un-provenanced q_lcb. Material-YES buy_no now requires an allowed
            # native NO source UNCONDITIONALLY — no edge-vs-gap escape hatch.
            # (The coverage VERDICT above is not a waiver: it is independent settled-
            # record evidence, not a self-referential test of the claimed q_lcb.)
            return (
                "ADMISSION_BUY_NO_CONSERVATIVE_EVIDENCE_MISSING:"
                f"yes_posterior={yes_posterior:.6f}:max={material_floor:.6f}:"
                f"no_q_lcb={q_lcb_value:.6f}:price={price:.6f}:"
                f"source={source or 'missing'}:"
                f"coverage_status={status or 'missing'}"
            )
    return None


def replacement_no_bound_certificate_matches(
    certificate: Mapping[str, object] | None,
    *,
    expected: Mapping[str, object] | None,
    q_direction: float,
    q_lcb: float,
    same_bin_yes_posterior: float,
    qkernel_execution_economics: Mapping[str, object] | None,
    probability_authority: str | None,
    posterior_id: int | str | None,
    condition_id: str | None,
) -> bool:
    """Verify one replacement posterior's binary YES/NO bound identity."""

    return replacement_no_bound_certificate_mismatch_reason(
        certificate,
        expected=expected,
        q_direction=q_direction,
        q_lcb=q_lcb,
        same_bin_yes_posterior=same_bin_yes_posterior,
        qkernel_execution_economics=qkernel_execution_economics,
        probability_authority=probability_authority,
        posterior_id=posterior_id,
        condition_id=condition_id,
    ) is None


def replacement_no_bound_certificate_mismatch_reason(
    certificate: Mapping[str, object] | None,
    *,
    expected: Mapping[str, object] | None,
    q_direction: float,
    q_lcb: float,
    same_bin_yes_posterior: float,
    qkernel_execution_economics: Mapping[str, object] | None,
    probability_authority: str | None,
    posterior_id: int | str | None,
    condition_id: str | None,
) -> str | None:
    """Name the first broken parent binding without weakening admission."""

    if not isinstance(certificate, Mapping) or not isinstance(expected, Mapping):
        return "parent_mapping_missing"
    if certificate.get("schema") != "replacement_native_no_bound_v1":
        return "certificate_schema"
    if certificate.get("probability_authority") != "replacement_0_1":
        return "certificate_probability_authority"
    if str(probability_authority or "").strip() != "replacement_0_1":
        return "served_probability_authority"
    if certificate.get("q_lcb_basis") != "fused_center_bootstrap_p05":
        return "certificate_q_lcb_basis"
    if certificate.get("q_ucb_role") != "fused_center_bootstrap_ucb":
        return "certificate_q_ucb_role"
    if certificate.get("q_mode") not in {"FUSED_NORMAL_FULL", "FUSED_NORMAL_PARTIAL"}:
        return "certificate_q_mode"
    if certificate.get("side") != "buy_no":
        return "certificate_side"
    certificate_posterior_id = certificate.get("posterior_id")
    if (
        isinstance(certificate_posterior_id, bool)
        or not isinstance(certificate_posterior_id, int)
        or certificate_posterior_id <= 0
    ):
        return "certificate_posterior_id"
    if isinstance(posterior_id, bool):
        return "served_posterior_id"
    try:
        receipt_posterior_id = int(posterior_id)
    except (TypeError, ValueError):
        return "served_posterior_id"
    if receipt_posterior_id != certificate_posterior_id:
        return "served_posterior_id_mismatch"
    certificate_condition_id = str(certificate.get("condition_id") or "").strip()
    if not certificate_condition_id or str(condition_id or "").strip() != certificate_condition_id:
        return "served_condition_id_mismatch"
    if not str(certificate.get("bin_id") or "").strip():
        return "certificate_bin_id"
    if not str(certificate.get("family_id") or "").strip():
        return "certificate_family_id"

    def valid_hash(value: object) -> bool:
        text = str(value or "").strip()
        if len(text) != 64:
            return False
        try:
            int(text, 16)
        except ValueError:
            return False
        return True

    for field in (
        "posterior_identity_hash",
        "bin_topology_hash",
        "joint_samples_hash",
    ):
        if not valid_hash(certificate.get(field)):
            return f"certificate_hash_field:{field}"
    bootstrap_draws = certificate.get("bootstrap_draws")
    if isinstance(bootstrap_draws, bool) or not isinstance(bootstrap_draws, int):
        return "certificate_bootstrap_draws"
    if bootstrap_draws < REPLACEMENT_BOOTSTRAP_MIN_DRAWS:
        return "certificate_bootstrap_draws_floor"
    certificate_hash = certificate.get("certificate_hash")
    if not valid_hash(certificate_hash):
        return "certificate_hash"
    certificate_body = dict(certificate)
    certificate_body.pop("certificate_hash", None)
    if stable_hash(certificate_body) != certificate_hash:
        return "certificate_hash_mismatch"
    for field in (
        "posterior_id",
        "posterior_identity_hash",
        "family_id",
        "bin_topology_hash",
        "condition_id",
        "bin_id",
        "q_mode",
        "q_lcb_basis",
        "q_ucb_role",
        "bootstrap_draws",
        "joint_samples_hash",
        "canonical_bound_hash",
    ):
        if expected.get(field) != certificate.get(field):
            return f"parent_field:{field}"
    for field in ("yes_q", "yes_q_ucb"):
        try:
            expected_value = float(expected[field])
            certificate_value = float(certificate[field])
        except (KeyError, TypeError, ValueError):
            return f"parent_probability_missing:{field}"
        if not math.isclose(
            expected_value,
            certificate_value,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            return f"parent_probability:{field}"
    try:
        expected_served_lcb = float(expected["side_q_lcb_served"])
        certificate_served_lcb = float(certificate["side_q_lcb_served"])
    except (KeyError, TypeError, ValueError):
        return "parent_probability_missing:side_q_lcb_served"
    if not math.isclose(
        expected_served_lcb,
        certificate_served_lcb,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        return "parent_probability:side_q_lcb_served"
    try:
        yes_q = float(certificate["yes_q"])
        yes_ucb = float(certificate["yes_q_ucb"])
        no_q = float(certificate["side_q_point"])
        no_lcb_raw = float(certificate["side_q_lcb_raw"])
        no_lcb_served = float(certificate["side_q_lcb_served"])
    except (KeyError, TypeError, ValueError):
        return "certificate_probability_fields"
    values = (yes_q, yes_ucb, no_q, no_lcb_raw, no_lcb_served)
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
        return "certificate_probability_range"
    if yes_ucb < yes_q or no_lcb_raw > no_q or no_lcb_served > no_lcb_raw + 1e-12:
        return "certificate_probability_order"

    def same(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)

    if not same(yes_q, same_bin_yes_posterior):
        return "served_yes_q"
    if not same(no_q, q_direction):
        return "served_no_q"
    if not same(no_q, 1.0 - yes_q):
        return "binary_complement_q"
    if not same(no_lcb_raw, 1.0 - yes_ucb):
        return "binary_complement_lcb"
    if bool(certificate.get("coverage_shrink_applied")) != (
        no_lcb_served < no_lcb_raw - 1e-12
    ):
        return "coverage_shrink_flag"
    if q_lcb > no_lcb_served + 1e-12:
        return "served_no_lcb_loosened"
    if same(q_lcb, no_lcb_served):
        return None
    economics = qkernel_execution_economics
    if not isinstance(economics, Mapping):
        return "qkernel_economics_missing"
    if economics.get("q_lcb_authority") != "qkernel_payoff_bound":
        return "qkernel_q_lcb_authority"
    if economics.get("probability_authority") != "qkernel_payoff_direct_route":
        return "qkernel_probability_authority"
    try:
        pre_q = float(economics["pre_qkernel_q_posterior"])
        pre_lcb = float(economics["pre_qkernel_q_lcb_5pct"])
        payoff_q = float(economics["payoff_q_point"])
        payoff_lcb = float(economics["payoff_q_lcb"])
    except (KeyError, TypeError, ValueError):
        return "qkernel_probability_fields"
    if not all(math.isfinite(value) for value in (pre_q, pre_lcb, payoff_q, payoff_lcb)):
        return "qkernel_probability_nonfinite"
    if not same(pre_q, no_q):
        return "qkernel_pre_q"
    if not same(pre_lcb, no_lcb_served):
        return "qkernel_pre_lcb"
    if not same(payoff_q, q_direction):
        return "qkernel_payoff_q"
    if not same(payoff_lcb, q_lcb):
        return "qkernel_payoff_lcb"
    return None


def replacement_probability_bundle_hash(
    *,
    posterior_id: int,
    posterior_identity_hash: str,
    family_id: str,
    bin_topology_hash: str,
    q_mode: str,
    q_lcb_basis: str,
    q_ucb_role: str,
    bootstrap_draws: int,
    joint_samples_hash: str,
    q: Mapping[str, object],
    q_lcb: Mapping[str, object],
    q_ucb: Mapping[str, object],
) -> str:
    """Hash the canonical replacement probability carrier used by both cert planes."""

    return stable_hash(
        {
            "posterior_id": int(posterior_id),
            "posterior_identity_hash": str(posterior_identity_hash),
            "family_id": str(family_id),
            "bin_topology_hash": str(bin_topology_hash),
            "q_mode": str(q_mode),
            "q_lcb_basis": str(q_lcb_basis),
            "q_ucb_role": str(q_ucb_role),
            "bootstrap_draws": int(bootstrap_draws),
            "joint_samples_hash": str(joint_samples_hash),
            "q": dict(q),
            "q_lcb": dict(q_lcb),
            "q_ucb": dict(q_ucb),
        }
    )


def replacement_no_bound_expected_from_parents(
    forecast: Mapping[str, object] | None,
    candidate: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Map DecisionKernel forecast/candidate parents to the bound validator schema."""

    if not isinstance(forecast, Mapping) or not isinstance(candidate, Mapping):
        return None
    field_map = {
        "posterior_id": "replacement_posterior_id",
        "posterior_identity_hash": "posterior_identity_hash",
        "family_id": "replacement_family_id",
        "bin_topology_hash": "replacement_bin_topology_hash",
        "q_mode": "replacement_q_mode",
        "q_lcb_basis": "replacement_q_lcb_basis",
        "q_ucb_role": "replacement_q_ucb_role",
        "bootstrap_draws": "replacement_bootstrap_draws",
        "joint_samples_hash": "replacement_joint_samples_hash",
        "canonical_bound_hash": "replacement_canonical_bound_hash",
    }
    expected = {target: forecast.get(source) for target, source in field_map.items()}
    q = forecast.get("replacement_q")
    q_lcb = forecast.get("replacement_q_lcb")
    q_ucb = forecast.get("replacement_q_ucb")
    if not all(isinstance(value, Mapping) and value for value in (q, q_lcb, q_ucb)):
        return None
    bin_id = candidate.get("replacement_no_bound_bin_id")
    if bin_id in (None, ""):
        return None
    try:
        canonical_bound_hash = replacement_probability_bundle_hash(
            posterior_id=int(expected["posterior_id"]),
            posterior_identity_hash=str(expected["posterior_identity_hash"]),
            family_id=str(expected["family_id"]),
            bin_topology_hash=str(expected["bin_topology_hash"]),
            q_mode=str(expected["q_mode"]),
            q_lcb_basis=str(expected["q_lcb_basis"]),
            q_ucb_role=str(expected["q_ucb_role"]),
            bootstrap_draws=int(expected["bootstrap_draws"]),
            joint_samples_hash=str(expected["joint_samples_hash"]),
            q=q,
            q_lcb=q_lcb,
            q_ucb=q_ucb,
        )
        expected_yes_q = float(q[bin_id])
        expected_yes_ucb = float(q_ucb[bin_id])
    except (KeyError, TypeError, ValueError):
        return None
    if canonical_bound_hash != expected.get("canonical_bound_hash"):
        return None
    expected.update(
        {
            "condition_id": candidate.get("condition_id"),
            "bin_id": bin_id,
            "yes_q": expected_yes_q,
            "yes_q_ucb": expected_yes_ucb,
            "side_q_lcb_served": candidate.get(
                "replacement_no_bound_served_lcb"
            ),
        }
    )
    if any(value in (None, "") for value in expected.values()):
        return None
    return expected


# ---------------------------------------------------------------------------
# Selection-aware q_lcb calibrator + per-city skill helper + would-admit logger.
#
# The selection calibrator is live entry law: if the promoted artifact does not license the side/cell
# it returns 0.0 and the candidate cannot clear edge. City skill is not live-promoted in this checkout
# because no current artifact exists; the only retained execution hook is the narrow loss-reduction
# stable-bad blocker when a caller explicitly supplies an artifact. Logging stays observational and
# cannot affect admission.
# ---------------------------------------------------------------------------

def selection_calibrated_admission_q_lcb(
    *,
    q_lcb: float | int | None,
    raw_side_prob: float | int | None,
    direction: str | None,
    lead_days: float = 1.0,
    bin_class: str = "nonmodal",
    own_side_cost: float | int | None = None,
    artifact=None,
    expected_posterior_version: str | None = None,
    temperature_metric: str = "high",
) -> float:
    """The admission q_lcb after the live selection-aware calibrator deflation.

    Deflates the served q_lcb to the calibrated lower bound for the adverse-selection tail, or 0.0
    fail-closed, via ``selection_calibrated_side_lcb`` so downstream ``edge_lcb = q_lcb - cost`` turns
    non-positive and the candidate is not admitted.

    Runtime errors fail closed to 0.0. A calibration fault must stop new entries, not restore the raw
    center-bootstrap q_lcb that caused the adverse-selection losses.
    """
    try:
        from src.decision.selection_calibrator import (
            DEFAULT_POSTERIOR_VERSION as _SC_DEFAULT_VER,
            selection_calibrated_side_lcb,
        )
        prior = float(q_lcb)
        if not math.isfinite(prior):
            return 0.0
        side = "NO" if str(direction or "").lower() == "buy_no" else "YES"
        margin = None
        if own_side_cost is not None and math.isfinite(float(own_side_cost)):
            margin = prior - float(own_side_cost)
        sc_lcb = float(
            selection_calibrated_side_lcb(
                raw_side_prob=float(raw_side_prob),
                prior_lcb=prior,
                side=side,
                lead_days=float(lead_days),
                bin_class=str(bin_class),
                admission_margin=margin,
                artifact=artifact,
                expected_posterior_version=expected_posterior_version or _SC_DEFAULT_VER,
                temperature_metric=temperature_metric,
            )
        )
        # 2026-06-23: compose the PRICE-CONDITIONED selection-curse deflation at ENTRY (the primary
        # curse site — the gate admits mid-price buy_no whose realized rate (~0.69) is well below its
        # claim (~0.83)). min() with the prior path: both only TIGHTEN. Absent/unarmed/out-of-support
        # -> raw (identity). See src/decision/selection_curse_bound.py + the counterfactual evidence.
        # PRICE BASIS: the bound is keyed on the RAW own-side ask. own_side_cost here is the candidate
        # execution_price (the raw native ask — distinct from the fee-adjusted c_cost_95pct), matching
        # the fitter's no_ask x-axis. The taker seams likewise pass the raw fresh ask. One basis.
        if own_side_cost is not None and math.isfinite(float(own_side_cost)):
            from src.decision.selection_curse_bound import corrected_side_q_lcb
            from src.decision.selection_curse_bound_loader import load_bound

            curse_lcb, _ = corrected_side_q_lcb(
                load_bound(),
                side=str(direction or ""),
                price=float(own_side_cost),
                raw_q_lcb=prior,
            )
            return min(sc_lcb, curse_lcb)
        return sc_lcb
    except Exception:  # noqa: BLE001
        return 0.0


def city_skill_block_rejection_reason(
    *,
    city: str | None,
    artifact=None,
    expected_posterior_version: str | None = None,
) -> str | None:
    """Block a candidate whose city is a confirmed temporally-stable loser.

    City-skill is not globally live-promoted without an artifact. This execution hook is intentionally
    narrow: it only acts when a caller supplies an artifact and that artifact marks the city as
    stable-bad. Missing artifact/context is outside this live path and must not masquerade as an
    enabled selector.
    """
    try:
        from src.decision.city_skill_gate import (
            DEFAULT_POSTERIOR_VERSION as _CSG_DEFAULT_VER,
            apply_city_skill_gate,
        )
        if artifact is None:
            return None
        if not city or not str(city).strip():
            return None
        verdict = apply_city_skill_gate(
            city=str(city),
            artifact=artifact,
            expected_posterior_version=expected_posterior_version or _CSG_DEFAULT_VER,
            require_stable_bad_to_block=True,
        )
        if verdict.basis == "CITY_SKILL_BLOCKED_STABLE_BAD":
            return (
                f"ADMISSION_CITY_SKILL_STABLE_BAD:city={city}:"
                f"prior_skill={verdict.prior_skill:.4f}:prior_n={verdict.n_g if hasattr(verdict, 'n_g') else verdict.prior_n}"
            )
        return None
    except Exception:  # noqa: BLE001 — never break admission for the skill gate.
        return None
