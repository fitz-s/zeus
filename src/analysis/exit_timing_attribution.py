# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: 2026-06-22 lifecycle design consult REQ-20260622-060011 (Pro
#   Extended) + operator mandate. SECOND attribution axis (exit timing), separable
#   from and composable with the entry-skill grader in settlement_skill_attribution.
"""Exit-timing attribution — grade the EXIT decision against real settlement.

The entry-skill grader (``settlement_skill_attribution``) answers "was the original
entry aligned with settlement reality if held?" — it grades settlement payoff vs the
immutable entry decision-q and is unchanged by this module.

This module adds the orthogonal question the operator demands ("sell before market
notice and gain is ALSO a good trade"): "given the entry existed, did the EXIT improve
realized value versus the counterfactual hold-to-settlement value for the shares it
closed?"

Per-closed-lot counterfactual (entry-independent — entry_cost cancels, so this never
double-counts entry skill):

    would_have_settled_value_usd = closed_shares * settlement_payoff_per_share
    net_exit_value_usd           = closed_shares * avg_exit_price - exit_fees_usd
    exit_alpha_usd               = net_exit_value_usd - would_have_settled_value_usd

and the decomposition realized_closed_lot_pnl = hold_counterfactual_pnl + exit_alpha_usd
holds identically. settlement_payoff_per_share is 1.0 when the held native token's side
won, else 0.0 (long YES/NO sell-to-close; see load-bearing assumption in the consult).

Forward, real-chain only: a grade is produced ONLY after verified settlement; missing
settlement / proceeds / exit-q are branded UNATTRIBUTABLE, never guessed.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("zeus")

SCHEMA_VERSION = 1

# Trigger reasons that constitute PREDICTIVE evidence against the held side (the exit
# was a model/physics/family-rank reversal call, not an operational/forced exit).
_PREDICTIVE_EXIT_TRIGGERS = frozenset(
    {
        "EDGE_REVERSED",
        "CI_SEPARATED_REVERSAL",
        "FAMILY_RANK",
        "FAMILY_RANK_REVERSAL",
        "PHYSICS_REVERSAL",
        "DAY0_HARD_FACT_EXIT",
        "SPURIOUS_MODEL_DIVERGENCE",
        "STRUCTURAL_WIN",
    }
)

# Triggers that are operational/forced rather than predictive — value delta is recorded
# but excluded from the exit-skill denominator (not a model-skill signal).
_ADMIN_RISK_EXIT_TRIGGERS = frozenset(
    {
        "ADMIN",
        "ADMIN_CLOSE",
        "RISK",
        "RISKGUARD",
        "KILL_SWITCH",
        "CUTOVER",
        "REDUCE_ONLY",
        "MANUAL",
    }
)


@dataclass(frozen=True)
class ExitTimingGrade:
    """Settlement-grounded grade of one closed lot's EXIT decision."""

    category: str
    exit_alpha_usd: float | None  # None only when value itself is unprovable
    net_exit_value_usd: float | None
    would_have_settled_value_usd: float | None
    is_skillful: bool
    counts_in_skill_denominator: bool
    rationale: str


def grade_exit_timing(
    *,
    closed_shares: float,
    avg_exit_price: float | None,
    exit_fees_usd: float = 0.0,
    settlement_won: bool | None,
    exit_q_authority_present: bool,
    exit_trigger_reason: str | None,
    materiality_usd: float = 0.01,
) -> ExitTimingGrade:
    """Grade one closed lot's exit timing against verified settlement.

    ``settlement_won`` is the held side's realized settlement outcome (True=won,
    False=lost, None=not yet settled). ``avg_exit_price`` is per-share proceeds
    (None when the exit fill/proceeds are not provable). ``exit_q_authority_present``
    is whether an immutable exit decision-q certificate exists for this exit (so the
    exit can be attributed to the EXIT decision, not the entry). ``exit_trigger_reason``
    is the exit cause (predictive vs operational).
    """

    trigger = (exit_trigger_reason or "").strip().upper()

    # --- unattributable gates (cannot grade value or skill) ---
    if settlement_won is None:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_SETTLEMENT_MISSING",
            exit_alpha_usd=None,
            net_exit_value_usd=None,
            would_have_settled_value_usd=None,
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale="no verified settlement yet; exit value cannot be graded against reality.",
        )
    if avg_exit_price is None:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_PROCEEDS_MISSING",
            exit_alpha_usd=None,
            net_exit_value_usd=None,
            would_have_settled_value_usd=None,
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale="settlement exists but exit fill/proceeds are not provable.",
        )

    # --- value computation (entry-independent counterfactual) ---
    settlement_payoff_per_share = 1.0 if settlement_won else 0.0
    net_exit_value_usd = closed_shares * float(avg_exit_price) - float(exit_fees_usd)
    would_have_settled_value_usd = closed_shares * settlement_payoff_per_share
    exit_alpha_usd = net_exit_value_usd - would_have_settled_value_usd

    base = dict(
        exit_alpha_usd=exit_alpha_usd,
        net_exit_value_usd=net_exit_value_usd,
        would_have_settled_value_usd=would_have_settled_value_usd,
    )

    # --- exit-q gate: value reported, but skill cannot be attributed ---
    if not exit_q_authority_present:
        return ExitTimingGrade(
            category="EXIT_UNATTRIBUTABLE_Q_MISSING",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit realized {exit_alpha_usd:+.4f} vs counterfactual hold, but no exit "
                "decision-q certificate — value reported, not attributable to model skill."
            ),
            **base,
        )

    # --- operational/forced exits: report delta, exclude from skill denominator ---
    if trigger in _ADMIN_RISK_EXIT_TRIGGERS:
        return ExitTimingGrade(
            category="ADMIN_OR_RISK_EXIT_VALUE_DELTA",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"forced exit (trigger={trigger}) realized {exit_alpha_usd:+.4f} vs hold; "
                "value recorded but excluded from model-skill denominator."
            ),
            **base,
        )

    # --- neutral: sold at ~settlement value ---
    if abs(exit_alpha_usd) <= float(materiality_usd):
        return ExitTimingGrade(
            category="NEUTRAL_EXIT",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit value {exit_alpha_usd:+.4f} within materiality "
                f"{materiality_usd:.4f}; no meaningful timing edge."
            ),
            **base,
        )

    predictive = trigger in _PREDICTIVE_EXIT_TRIGGERS

    # --- positive alpha: sold above the counterfactual hold value ---
    if exit_alpha_usd > 0:
        if predictive:
            return ExitTimingGrade(
                category="SKILLFUL_REVERSAL_EXIT",
                is_skillful=True,
                counts_in_skill_denominator=True,
                rationale=(
                    f"predictive exit (trigger={trigger}) captured {exit_alpha_usd:+.4f} vs "
                    "hold-to-settlement — sold before the market priced the reversal; real exit skill."
                ),
                **base,
            )
        return ExitTimingGrade(
            category="LUCKY_EXIT_SAVED_LOSS",
            is_skillful=False,
            counts_in_skill_denominator=False,
            rationale=(
                f"exit saved {exit_alpha_usd:+.4f} vs hold but trigger={trigger or 'none'} is not "
                "predictive evidence against the held side — lucky, not skill."
            ),
            **base,
        )

    # --- negative alpha with predictive q-authority exit: a genuine skill MISS ---
    return ExitTimingGrade(
        category="PREMATURE_EXIT_COST",
        is_skillful=False,
        counts_in_skill_denominator=True,
        rationale=(
            f"predictive exit (trigger={trigger}) gave up {exit_alpha_usd:+.4f} vs holding to "
            "settlement — premature; counts as an exit-skill MISS."
        ),
        **base,
    )


# ---------------------------------------------------------------------------
# Persistence + runner — the SOLE writer of exit_timing_attribution
# ---------------------------------------------------------------------------
#
# The exit-timing pass of the settlement attribution job. It reads the ENTRY
# grader's verified settlement truth (settlement_attribution.won — present only
# for positions with a VERIFIED settlement) joined to the exited position's
# proceeds (trades.position_current.exit_price/exit_reason/shares), grades the
# EXIT decision, and UPSERTs one idempotent row per exited position.
#
# INV-37: a single world connection with ``trades`` ATTACHed (the same conn the
# entry grader uses); no independent second connection is opened here.
#
# v1 authority: ``exit_q_authority_present = bool(exit_reason)`` — a recorded exit
# decision reason is the decision authority available today; the immutable exit
# decision-q certificate (a strengthening increment) will refine skill attribution.


def _exit_row_exists(world_conn: sqlite3.Connection, position_id: str) -> bool:
    return (
        world_conn.execute(
            "SELECT 1 FROM exit_timing_attribution WHERE position_id = ? LIMIT 1",
            (position_id,),
        ).fetchone()
        is not None
    )


def persist_exit_timing_grade(
    world_conn: sqlite3.Connection,
    *,
    position_id: str,
    condition_id: Optional[str],
    city: Optional[str],
    target_date: Optional[str],
    temperature_metric: Optional[str],
    direction: Optional[str],
    closed_shares: float,
    avg_exit_price: Optional[float],
    exit_fees_usd: float,
    exit_reason: Optional[str],
    exit_q_authority_present: bool,
    settlement_won: Optional[bool],
    grade: ExitTimingGrade,
    now_utc: Optional[datetime] = None,
) -> None:
    """Idempotent UPSERT of one exit-timing grade (sole writer)."""
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)
    world_conn.execute(
        """
        INSERT INTO exit_timing_attribution (
            attribution_id, position_id, condition_id, city, target_date,
            temperature_metric, direction, category, closed_shares, avg_exit_price,
            exit_fees_usd, exit_reason, exit_q_authority_present, settlement_won,
            net_exit_value_usd, would_have_settled_value_usd, exit_alpha_usd,
            is_skillful, counts_in_skill_denominator, rationale, graded_at,
            schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            category = excluded.category,
            closed_shares = excluded.closed_shares,
            avg_exit_price = excluded.avg_exit_price,
            exit_fees_usd = excluded.exit_fees_usd,
            exit_reason = excluded.exit_reason,
            exit_q_authority_present = excluded.exit_q_authority_present,
            settlement_won = excluded.settlement_won,
            net_exit_value_usd = excluded.net_exit_value_usd,
            would_have_settled_value_usd = excluded.would_have_settled_value_usd,
            exit_alpha_usd = excluded.exit_alpha_usd,
            is_skillful = excluded.is_skillful,
            counts_in_skill_denominator = excluded.counts_in_skill_denominator,
            rationale = excluded.rationale,
            graded_at = excluded.graded_at
        """,
        (
            str(uuid.uuid4()), position_id, condition_id, city, target_date,
            temperature_metric, direction, grade.category, closed_shares,
            avg_exit_price, exit_fees_usd, exit_reason,
            int(exit_q_authority_present),
            (None if settlement_won is None else int(settlement_won)),
            grade.net_exit_value_usd, grade.would_have_settled_value_usd,
            grade.exit_alpha_usd, int(grade.is_skillful),
            int(grade.counts_in_skill_denominator), grade.rationale,
            now_utc.isoformat(), SCHEMA_VERSION,
        ),
    )


def run_exit_timing_attribution(
    world_conn: sqlite3.Connection,
    *,
    now_utc: Optional[datetime] = None,
    only_new: bool = False,
) -> dict:
    """Grade every EXITED position's exit timing vs verified settlement (idempotent).

    Reads settlement_attribution (verified settlement truth, entry grader output)
    JOIN trades.position_current (exit proceeds) for positions that EXITED before
    settlement (phase economically_closed / admin_closed). Held-to-settlement
    ('settled') positions made no exit decision and are excluded. The SOLE writer
    of exit_timing_attribution. Returns a stats dict (graded, by_category,
    total_exit_alpha_usd). Read-only over settlement_attribution/position_current.
    """
    if now_utc is None:
        now_utc = datetime.now(tz=timezone.utc)

    rows = world_conn.execute(
        """
        SELECT sa.position_id, sa.condition_id, sa.city, sa.target_date,
               sa.temperature_metric, sa.direction, sa.won,
               pc.exit_price, pc.exit_reason, pc.shares
        FROM settlement_attribution AS sa
        JOIN trades.position_current AS pc ON pc.position_id = sa.position_id
        WHERE pc.phase IN ('economically_closed', 'admin_closed')
          AND pc.exit_price IS NOT NULL
        """
    ).fetchall()

    graded = 0
    skipped = 0
    by_category: dict[str, int] = {}
    total_exit_alpha = 0.0
    for (
        position_id, condition_id, city, target_date, temperature_metric,
        direction, won, exit_price, exit_reason, shares,
    ) in rows:
        if only_new and _exit_row_exists(world_conn, str(position_id)):
            skipped += 1
            continue
        reason = (exit_reason or "").strip()
        grade = grade_exit_timing(
            closed_shares=float(shares or 0.0),
            avg_exit_price=(None if exit_price is None else float(exit_price)),
            exit_fees_usd=0.0,  # per-exit fees not yet on position_current; refine w/ exit cert
            settlement_won=(None if won is None else bool(won)),
            exit_q_authority_present=bool(reason),
            exit_trigger_reason=reason or None,
        )
        persist_exit_timing_grade(
            world_conn,
            position_id=str(position_id), condition_id=condition_id, city=city,
            target_date=target_date, temperature_metric=temperature_metric,
            direction=direction, closed_shares=float(shares or 0.0),
            avg_exit_price=(None if exit_price is None else float(exit_price)),
            exit_fees_usd=0.0, exit_reason=(reason or None),
            exit_q_authority_present=bool(reason),
            settlement_won=(None if won is None else bool(won)),
            grade=grade, now_utc=now_utc,
        )
        graded += 1
        by_category[grade.category] = by_category.get(grade.category, 0) + 1
        if grade.exit_alpha_usd is not None:
            total_exit_alpha += grade.exit_alpha_usd

    return {
        "graded": graded,
        "skipped_existing": skipped,
        "exited_positions": len(rows),
        "by_category": by_category,
        "total_exit_alpha_usd": round(total_exit_alpha, 4),
    }
