"""Shared live-trade admission primitives.

These helpers express objective-level live-money constraints that are broader
than per-family ranking. They do not change q, price, FDR, Kelly, or venue state.
"""

from __future__ import annotations

from collections.abc import Mapping
import math

# BUY-NO MATERIAL-BIN VOCABULARY: this is stricter than the replacement certificate
# coverage predicate. A typed INSUFFICIENT_DATA verdict proves the coverage authority ran
# and is enough for the certificate bridge to continue into ordinary live gates, but it
# is not enough to waive the special material-bin buy-NO evidence requirement.
from src.calibration.settlement_backward_coverage import (
    settlement_coverage_refutes_claim,
)


# Operator objective for ordinary binary replacement/NO-side candidates: real
# participating trades must settle with stable win-rate greater than 51% after
# costs. Q-kernel center-buy YES is a different Arrow-Debreu point-bin contract:
# a single exact-bin YES can be profitable with side probability below 51% when
# it is the family-efficient claim. It uses the q-kernel quality floor below
# instead of this binary replacement floor.
LIVE_DIRECTION_WIN_RATE_FLOOR = 0.51
LIVE_NEAR_SETTLED_ENTRY_PRICE_CEILING = 0.99
LIVE_QKERNEL_CENTER_YES_MIN_Q_LCB = 0.15

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


def qkernel_center_yes_quality_floor() -> float:
    """Minimum conservative exact-bin YES probability for live q-kernel center buys."""

    return float(LIVE_QKERNEL_CENTER_YES_MIN_Q_LCB)


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

    Ordinary buy-side entries keep the binary selected-side win-rate floor. The
    q-kernel center-buy YES lane is exempt from the 51% binary floor because it
    buys one exact outcome in a multi-bin family; using the binary floor there
    mechanically starves legitimate center YES trades and pushes the optimizer
    toward NO. It still needs a real conservative probability floor so cheap
    longshot tails cannot pass on ROI optics alone.
    """

    direction_value = getattr(direction, "value", direction)
    direction_text = str(direction_value or "").strip().lower()
    strategy_text = str(strategy_key or "").strip()
    authority_text = str(selection_authority_applied or "").strip()
    is_qkernel_center_yes = (
        direction_text == "buy_yes"
        and strategy_text == "center_buy"
        and authority_text == "qkernel_spine"
        and isinstance(qkernel_execution_economics, Mapping)
        and str(qkernel_execution_economics.get("source") or "").strip() == "qkernel_spine"
    )
    if is_qkernel_center_yes:
        reason = live_win_rate_floor_rejection_reason(
            q_lcb=q_lcb,
            floor=qkernel_center_yes_quality_floor(),
        )
        if reason is None:
            return None
        return reason.replace(
            "ADMISSION_WIN_RATE_FLOOR",
            "ADMISSION_QKERNEL_CENTER_YES_QUALITY_FLOOR",
            1,
        )
    return live_win_rate_floor_rejection_reason(q_lcb=q_lcb, floor=floor)


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
