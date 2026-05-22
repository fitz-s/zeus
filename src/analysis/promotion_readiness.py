# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/SESSION_CLOSURE_VERDICT.md
#                  + commit 5754f41e0f (unified evidence-tier authority gates, #279)
"""PromotionReadinessValidator — read-only operator advisory for strategy promotion.

Composes three existing signals into a single READY / NOT_READY verdict per strategy:
  (a) EvidenceReport credible-interval / promotion signal
  (b) LiveReadinessTribunal PROMOTE / HOLD / DEMOTE verdict logic (pure-compute, no DB write)
  (c) SettlementCaptureVerifier.check_pre_promotion_gate COHERENT-count gate

HARD CONSTRAINTS:
- The validator NEVER writes a tier, never calls adjudicate() with a live conn.
  It emits a recommendation only; the operator applies it.
- A PROMOTE recommendation targeting a live tier (>= LIVE_PILOT_TINY) MUST have a
  non-empty operator_ref supplied at call time; otherwise ValueError is raised
  (mirrors the tribunal's operator-gate invariant at the recommendation layer).
- Settlement gate is per-city/metric; strategies that do not use the settlement
  capture table are exempt (pass requires_settlement_gate=False at construction).
  N/A does NOT default to blocking; it defaults to PASS to avoid marking all
  non-settlement strategies as permanently NOT_READY.

Design note: signal (b) delegates to promotion_predicate() from live_readiness_tribunal —
the single canonical promotion gate — rather than re-deriving the inequality inline.
This makes predicate divergence between the tribunal and the validator impossible
(Fitz #4 / #279 finding #1). adjudicate() calls the same function.
See SESSION_CLOSURE_VERDICT.md §Part B + PROMO_VALIDATOR_CRITIC.md MAJOR finding.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.analysis.evidence_report import EvidenceReport
from src.analysis.live_readiness_tribunal import promotion_predicate
from src.contracts.evidence_tier import EvidenceTier


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------

class ReadinessVerdict(str, Enum):
    READY = "READY"
    NOT_READY = "NOT_READY"


# ---------------------------------------------------------------------------
# Per-signal result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalResult:
    """Outcome + rationale for one input signal."""
    signal_name: str
    passed: bool
    rationale: str


# ---------------------------------------------------------------------------
# Composite verdict object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromotionReadinessReport:
    """Operator-reviewable verdict for a single strategy.

    Fields
    ------
    strategy_id:
        Strategy key assessed.
    verdict:
        READY (all three signals pass) or NOT_READY (any one fails).
    tier_current:
        EvidenceTier at time of assessment.
    tier_target:
        Recommended next tier on READY, or tier_current on NOT_READY.
    signals:
        Per-signal breakdown: evidence_ci, tribunal, settlement_gate.
    operator_ref_required:
        True when tier_target >= LIVE_PILOT_TINY (operator must supply operator_ref
        before persisting any tier change).
    summary:
        Human-readable one-line summary for operator review.
    """
    strategy_id: str
    verdict: ReadinessVerdict
    tier_current: EvidenceTier
    tier_target: EvidenceTier
    signals: tuple[SignalResult, ...]
    operator_ref_required: bool
    summary: str


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

class PromotionReadinessValidator:
    """Compose evidence / tribunal / settlement signals into a promotion recommendation.

    Parameters
    ----------
    tier_required_for_live:
        Minimum EvidenceTier at which a strategy becomes live-eligible.
        REQUIRED — no default. Callers MUST supply the per-strategy value from
        StrategyProfile.evidence_tier_required_for_live (or equivalent registry
        source). A loose default here (e.g. LIVE_PILOT_TINY) would silently apply
        the cheapest gate to LIVE_NORMAL-required strategies, yielding false-READY.
        (#279 finding #1 / Fitz #4: single source of tier authority.)
    cost_of_capital:
        Additional margin above breakeven required for the CI gate to pass
        (mirrors the tribunal's cost_of_capital parameter).
    requires_settlement_gate:
        True for strategies whose promotion depends on settlement timestamp
        coherence (settlement_capture, resolution_window_maker).
        False for all others — settlement signal is N/A and does not block.
    coherent_threshold:
        Minimum COHERENT count required by check_pre_promotion_gate.
        When None the function reads from config/settings.json (default 5).
    """

    def __init__(
        self,
        *,
        tier_required_for_live: EvidenceTier,
        cost_of_capital: float = 0.0,
        requires_settlement_gate: bool = False,
        coherent_threshold: Optional[int] = None,
    ) -> None:
        self._tier_required_for_live = tier_required_for_live
        self._cost_of_capital = cost_of_capital
        self._requires_settlement_gate = requires_settlement_gate
        self._coherent_threshold = coherent_threshold

    def assess(
        self,
        report: EvidenceReport,
        *,
        city: Optional[str] = None,
        temperature_metric: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
        operator_ref: Optional[str] = None,
    ) -> PromotionReadinessReport:
        """Assess promotion readiness for one strategy.

        Parameters
        ----------
        report:
            EvidenceReport for this strategy (build via build_evidence_report or
            construct directly in tests).
        city:
            City for settlement gate query. Required when requires_settlement_gate=True.
        temperature_metric:
            'high' or 'low' for settlement gate query.
        conn:
            DB connection for the settlement gate query when requires_settlement_gate=True.
            When None and requires_settlement_gate=True, the gate opens a read-only
            connection internally (passes None to check_pre_promotion_gate which handles it).
        operator_ref:
            Must be a non-empty string when the recommended tier_target is >= LIVE_PILOT_TINY.
            Raises ValueError if missing/whitespace in that case (fail-closed; mirrors
            the tribunal's operator-gate invariant at the recommendation layer).

        Returns
        -------
        PromotionReadinessReport
            Recommendation only. No tier is written; no DB row is inserted.

        Raises
        ------
        ValueError
            If tier_target would be >= LIVE_PILOT_TINY and operator_ref is missing.
        """
        tier_current = report.tier_observed
        breakeven = report.breakeven_win_rate
        ci_lower = report.ci_lower

        # ── Signal (a): EvidenceReport CI ─────────────────────────────────────
        ci_passes, ci_rationale = self._eval_ci_signal(
            tier_current, ci_lower, breakeven
        )

        # ── Signal (b): Tribunal promotion predicate (pure-compute, no DB write) ─
        tribunal_passes, tribunal_rationale = self._eval_tribunal_signal(
            tier_current, ci_lower, breakeven
        )

        # ── Signal (c): Settlement coherence gate ─────────────────────────────
        settlement_passes, settlement_rationale = self._eval_settlement_signal(
            city=city,
            temperature_metric=temperature_metric,
            conn=conn,
        )

        signals = (
            SignalResult("evidence_ci", ci_passes, ci_rationale),
            SignalResult("tribunal", tribunal_passes, tribunal_rationale),
            SignalResult("settlement_gate", settlement_passes, settlement_rationale),
        )

        all_pass = ci_passes and tribunal_passes and settlement_passes

        # Determine tier_target
        if all_pass and tier_current < self._tier_required_for_live:
            tier_target = EvidenceTier(min(7, tier_current.value + 1))
        else:
            tier_target = tier_current

        verdict = ReadinessVerdict.READY if all_pass else ReadinessVerdict.NOT_READY

        # Operator-ref guard: raise only when recommending a CROSSING into a live tier.
        # A strategy already at or above LIVE_PILOT_TINY has verdict=NOT_READY (promotion
        # is moot); raising there would block routine read-only health checks on live
        # strategies without providing any safety value.
        crossing_into_live = (
            tier_target >= EvidenceTier.LIVE_PILOT_TINY
            and tier_target > tier_current
        )
        if crossing_into_live and not (operator_ref or "").strip():
            raise ValueError(
                f"Operator-gate violation: PromotionReadinessValidator recommends "
                f"tier_target={tier_target.name} (>= LIVE_PILOT_TINY) but operator_ref "
                f"is missing or blank. Supply operator_ref= to confirm operator approval."
            )

        operator_ref_required = crossing_into_live
        failing = [s.signal_name for s in signals if not s.passed]
        if verdict == ReadinessVerdict.READY:
            summary = (
                f"READY: all signals pass; recommend {tier_current.name} → {tier_target.name}"
                + (f" (operator_ref={operator_ref!r})" if operator_ref_required else "")
            )
        else:
            summary = f"NOT_READY: failing signals: {', '.join(failing)}"

        return PromotionReadinessReport(
            strategy_id=report.strategy_id,
            verdict=verdict,
            tier_current=tier_current,
            tier_target=tier_target,
            signals=signals,
            operator_ref_required=operator_ref_required,
            summary=summary,
        )

    # ── Private signal evaluators ──────────────────────────────────────────

    def _eval_ci_signal(
        self,
        tier_current: EvidenceTier,
        ci_lower: Optional[float],
        breakeven: float,
    ) -> tuple[bool, str]:
        """Pass when ci_lower > breakeven + cost_of_capital and tier < required."""
        if tier_current >= self._tier_required_for_live:
            return False, (
                f"NOT_READY (moot): tier_observed={tier_current.name} already >= "
                f"required={self._tier_required_for_live.name}; promotion not needed"
            )
        if ci_lower is None:
            return False, "FAIL: ci_lower is None (n_settled=0); no statistical evidence yet"
        threshold = breakeven + self._cost_of_capital
        if ci_lower > threshold:
            return True, (
                f"PASS: ci_lower={ci_lower:.4f} > breakeven={breakeven:.4f} + "
                f"cost_of_capital={self._cost_of_capital:.4f}"
            )
        return False, (
            f"FAIL: ci_lower={ci_lower:.4f} <= breakeven={breakeven:.4f} + "
            f"cost_of_capital={self._cost_of_capital:.4f}; insufficient evidence"
        )

    def _eval_tribunal_signal(
        self,
        tier_current: EvidenceTier,
        ci_lower: Optional[float],
        breakeven: float,
    ) -> tuple[bool, str]:
        """Delegate to promotion_predicate() — the single canonical promotion gate.

        promotion_predicate() lives in live_readiness_tribunal and is the same
        function adjudicate() uses; both call sites share one source of truth so
        divergence is structurally impossible (Fitz #4 / #279 finding #1).
        """
        would_promote = promotion_predicate(
            tier_current,
            self._tier_required_for_live,
            ci_lower,
            breakeven,
            self._cost_of_capital,
        )
        if would_promote:
            threshold = breakeven + self._cost_of_capital
            return True, (
                f"PROMOTE: ci_lower={ci_lower:.4f} > threshold={threshold:.4f}; "
                f"tribunal would emit PROMOTE {tier_current.name} → next tier"
            )
        # Not promoting — produce a human-readable rationale for the operator.
        if tier_current >= self._tier_required_for_live:
            return False, (
                f"HOLD (moot): tier_observed={tier_current.name} >= "
                f"required={self._tier_required_for_live.name}"
            )
        if ci_lower is None:
            return False, "HOLD: no settled evidence (ci_lower=None)"
        threshold = breakeven + self._cost_of_capital
        if ci_lower < breakeven - self._cost_of_capital:
            return False, (
                f"DEMOTE: ci_lower={ci_lower:.4f} < breakeven={breakeven:.4f} - "
                f"cost_of_capital={self._cost_of_capital:.4f}; underperformance signal"
            )
        return False, (
            f"HOLD: ci_lower={ci_lower:.4f} not above threshold={threshold:.4f}"
        )

    def _eval_settlement_signal(
        self,
        *,
        city: Optional[str],
        temperature_metric: Optional[str],
        conn: Optional[sqlite3.Connection],
    ) -> tuple[bool, str]:
        """Settlement COHERENT count gate.

        When requires_settlement_gate=False: always PASS (N/A).
        When True but city/metric missing: FAIL with explanation.
        """
        if not self._requires_settlement_gate:
            return True, "N/A: strategy does not require settlement gate"
        if not city or not temperature_metric:
            return False, (
                "FAIL: requires_settlement_gate=True but city/temperature_metric not supplied"
            )
        from src.contracts.settlement_capture_verifier import check_pre_promotion_gate
        passed = check_pre_promotion_gate(
            city,
            temperature_metric,
            conn=conn,
            threshold=self._coherent_threshold,
        )
        threshold_display = self._coherent_threshold if self._coherent_threshold is not None else "settings/5"
        if passed:
            return True, (
                f"PASS: COHERENT count >= {threshold_display} for {city}/{temperature_metric}"
            )
        return False, (
            f"FAIL: COHERENT count < {threshold_display} for {city}/{temperature_metric}; "
            f"insufficient settlement timestamp coherence"
        )
