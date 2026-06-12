"""Shared live-trade admission primitives.

These helpers express objective-level live-money constraints that are broader
than per-family ranking. They do not change q, price, FDR, Kelly, or venue state.
"""

from __future__ import annotations

import math


# Operator objective: real participating trades must settle with stable win-rate
# greater than 51% after costs. Positive-EV low-probability lottery legs remain
# valid research/shadow evidence, but they are not live-money entries.
LIVE_DIRECTION_WIN_RATE_FLOOR = 0.51

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

# SINGLE AUTHORITY (twin-authority reconciliation #7, 2026-06-11): the settlement-backward
# coverage verdict statuses that LICENSE a fused-bootstrap q_lcb for live. ONE home — this
# module — consumed by BOTH the admission gate below AND the adapter's cert credential
# (event_reactor_adapter re-exports it as _FUSED_BOOTSTRAP_COVERAGE_LICENSING_STATUSES).
# LICENSED  = realized settled win-rate backs the claimed q_lcb within tolerance.
# UNLICENSED = the record refuted the raw claim and the K3 shrink to realized-minus-1pp
#              was the verdict's output — the (shrunk) q_lcb is settled-record-backed.
# INSUFFICIENT_DATA (and None/UNEVALUATED) carry NO realized backing → never license.
# Category inversion this kills: before reconciliation a record-BACKED bootstrap q_lcb
# kept source=FORECAST_BOOTSTRAP → admission rejected, while a record-REFUTED one got
# shrunk → branded SETTLEMENT_ISOTONIC → admission accepted. The verdict, not the brand,
# is the evidence.
SETTLEMENT_COVERAGE_LICENSING_STATUSES = frozenset({"LICENSED", "UNLICENSED"})


def live_win_rate_floor_rejection_reason(
    *,
    q_lcb: float | int | None,
    floor: float = LIVE_DIRECTION_WIN_RATE_FLOOR,
) -> str | None:
    """Return a live admission blocker when the direction LCB is below floor."""

    try:
        q_value = float(q_lcb)
        floor_value = float(floor)
    except (TypeError, ValueError):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=missing:min={float(floor):.4f}"
    if not math.isfinite(q_value):
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb=nonfinite:min={floor_value:.4f}"
    if not math.isfinite(floor_value) or floor_value <= 0.0 or floor_value >= 1.0:
        raise ValueError(f"live win-rate floor must be in (0, 1), got {floor!r}")
    if q_value < floor_value:
        return f"ADMISSION_WIN_RATE_FLOOR:q_lcb={q_value:.4f}:min={floor_value:.4f}"
    return None


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
    material_yes_posterior: float = LIVE_BUY_NO_MATERIAL_YES_POSTERIOR,
    allowed_lcb_sources: frozenset[str] = LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES,
) -> str | None:
    """Block material-bin buy-NO without evidence-backed conservative NO LCB.

    ``q_direction`` is the candidate-direction posterior. ``same_bin_yes_posterior``
    must be supplied from an independently materialized YES-bin probability; this
    guard must never infer YES from a NO candidate by complement arithmetic. The
    guard is deliberately one-way: it never creates a trade and it does not touch
    buy-YES.

    ``settlement_coverage_status`` (twin-authority reconciliation #7, 2026-06-11) is
    the family's settlement-backward coverage VERDICT status — the SAME flag-
    independent verdict the cert credential licenses on (computed once per family on
    the replacement path; the caller threads the status string, keeping this module
    pure). When the q_lcb source is not in the allow-list, a verdict in
    SETTLEMENT_COVERAGE_LICENSING_STATUSES ({LICENSED, UNLICENSED}) admits: the
    settled record evaluated this scope and backed the (possibly shrunk) claim.
    INSUFFICIENT_DATA / None reject exactly as before, with the status appended to
    the reason for provenance. This is reconciliation, not gate-weakening: the
    evidence bar (settled-record backing) is unchanged — only the vocabulary the
    gate reads is unified with the cert layer's.
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

    if yes_posterior >= material_floor:
        source = str(q_lcb_calibration_source or "").strip()
        if source not in allowed_lcb_sources:
            # Twin-authority reconciliation #7 (2026-06-11): a settlement-backward
            # coverage verdict the settled record evaluated (LICENSED, or UNLICENSED
            # where the shrink was the verdict's output) is settled-record backing —
            # the SAME evidence bar the source allow-list expresses, read from the
            # cert layer's vocabulary instead of the q_lcb brand. Kills the category
            # inversion where a record-BACKED bootstrap q_lcb was rejected while a
            # record-REFUTED one (re-branded by the shrink) was accepted.
            status = str(settlement_coverage_status or "").strip()
            if status in SETTLEMENT_COVERAGE_LICENSING_STATUSES:
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
