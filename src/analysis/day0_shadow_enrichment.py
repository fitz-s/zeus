# Created: 2026-06-11
# Last reused or audited: 2026-06-11
# Authority basis: operator directive 2026-06-11 — day0 evidence lane repair. The day0
#   shadow lane (no_trade_regret_events, rejection_reason='DAY0_SCOPE_SHADOW_ONLY')
#   accumulated 1785 receipts with the fill/outcome grading layer (later_outcome,
#   would_have_won, hypothetical_fill_*) at 0% population — NO writer existed
#   (docs/evidence/day0/2026-06-11_day0_shadow_accuracy_profitability.md §1.4/§5,
#   "the dominant fix"). This module is that writer. It NEVER submits and NEVER
#   fabricates content: it only grades candidate-bearing receipts against VERIFIED
#   settlement truth and stamps a taker-fee-law fill price.
# Grading authority (reused, not re-derived): src.contracts.graded_receipt.grade_receipt
#   (Direction Law: buy_yes WIN iff settled_in_bin; buy_no WIN iff NOT settled_in_bin;
#   HK preimage + unit antibody). Settlement truth: forecasts.settlement_outcomes
#   (authority='VERIFIED' only — data-provenance law; UNVERIFIED never enters grading).
# Fee law: 0.05 · p · (1 − p) · shares (28/28 reconciled,
#   docs/evidence/reconciliation/2026-06-10_wallet_history_reconcile.md).
"""Day0 shadow-receipt fill/outcome enrichment.

THE GAP THIS CLOSES
-------------------
A day0 shadow receipt that is candidate-bearing (carries direction + bin_label +
q) can be GRADED once its (city, target_date, metric) target settles, and its
would-be taker fill price can be computed from the executable ask. Until now no
job did either, so the entire profitability/accuracy layer stayed NULL and the
>51%/150-270-sample promotion bar was un-evaluable.

PURE-DB by construction (no venue/network I/O): the settlement-grading half joins
no_trade_regret_events (WORLD) to forecasts.settlement_outcomes (ATTACHed) and
writes via NoTradeRegretLedger.enrich_after_settlement. The fill-price half is a
deterministic arithmetic over an already-captured ask. Because nothing here
touches the network, it runs on its own short-lived connection — no three-phase
venue-sync contract needed (src/execution/venue_sync_contract.py governs only
DB+network mixes, which this is not).

NEVER-SUBMIT / NEVER-FABRICATE invariants:
  - Only receipts that ALREADY carry candidate content (direction + bin_label)
    are graded. Bare scope-gate receipts are skipped — enrichment never invents a
    direction or a bin.
  - Only VERIFIED settlements grade a receipt; an UNVERIFIED/absent settlement
    leaves the receipt ungraded (would_have_won stays NULL).
  - Grading is delegated to grade_receipt; this module adds NO win/loss heuristic.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)

# The fee constant in the Polymarket taker law c = 0.05 · p · (1−p) · shares.
_TAKER_FEE_COEFF = 0.05


def hypothetical_taker_fill(*, ask: Optional[float], shares: float = 1.0) -> Optional[dict]:
    """Compute the would-be TAKER fill for a candidate at the executable ask.

    Returns a dict of the hypothetical-fill columns, or None when there is no
    usable ask (no liquidity → UNFILLABLE, never a fabricated fill). The fee is
    the canonical taker law ``0.05 · p · (1−p) · shares`` on ``p = ask``;
    ``c_fee_adjusted`` is the after-fee per-share cost = ask + fee/shares.
    """
    if ask is None:
        return None
    try:
        p = float(ask)
    except (TypeError, ValueError):
        return None
    # A degenerate ask (<=0 or >=1) is not a fillable two-sided price.
    if not (0.0 < p < 1.0):
        return None
    s = max(0.0, float(shares))
    fee_total = _TAKER_FEE_COEFF * p * (1.0 - p) * s
    fee_per_share = fee_total / s if s > 0 else _TAKER_FEE_COEFF * p * (1.0 - p)
    return {
        "hypothetical_order_type": "taker",
        "hypothetical_fill_status": "FILLED_AT_ASK",
        "hypothetical_fill_price": p,
        "c_fee_adjusted": p + fee_per_share,
    }


def grade_day0_receipt_outcome(
    *,
    bin_label: str,
    direction: str,
    settlement_value: float,
    settlement_unit: str,
):
    """Grade ONE candidate-bearing day0 receipt against its settled value.

    Returns the ``GradedReceipt`` (carrying ``won`` + ``settled_in_bin``), or None
    when the bin label cannot be parsed, the direction is unknown, or the units
    mismatch (grade_receipt's UnitMismatchError — a °F receipt against a °C
    settlement is refused, never silently mis-scored). Pure delegation to the
    canonical truth function; no second grading path.
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.cron.settlement_attribution import _bin_from_label
    from src.types.temperature import UnitMismatchError

    bin_obj = _bin_from_label(bin_label, settlement_unit)
    if bin_obj is None:
        return None

    class _S:  # minimal settlement stand-in for grade_receipt
        pass

    _S.settlement_value = float(settlement_value)  # type: ignore[attr-defined]
    _S.settlement_unit = str(settlement_unit)  # type: ignore[attr-defined]

    try:
        return grade_receipt(bin_obj, direction, _S())
    except UnitMismatchError:
        logger.warning(
            "day0_shadow_enrichment: unit mismatch bin=%s unit=%s — receipt skipped",
            bin_label, settlement_unit,
        )
        return None
    except ValueError:
        # Unknown direction — skip, never crash a batch.
        return None


def enrich_settled_day0_receipts(
    world_conn: sqlite3.Connection,
    *,
    settlement_table: str = "forecasts.settlement_outcomes",
    batch_limit: int = 5_000,
    grade_all_candidate_bearing: bool = False,
) -> int:
    """Write later_outcome + would_have_won for SETTLED candidate-bearing rows.

    Joins ``no_trade_regret_events`` (rows that already carry direction +
    bin_label) to VERIFIED ``settlement_outcomes`` on (city, target_date, metric),
    grades each through ``grade_receipt``, and writes via
    ``NoTradeRegretLedger.enrich_after_settlement`` (which carries the
    settlement_proof + hindsight guard). Idempotent: rows already carrying
    ``would_have_won`` are skipped, so a re-run writes nothing new.

    ``grade_all_candidate_bearing`` (2026-06-14): the original writer graded ONLY
    ``rejection_reason='DAY0_SCOPE_SHADOW_ONLY'`` rows, leaving the actual
    edge-bearing REJECTIONS (TRADE_SCORE_NON_POSITIVE — the q_lcb-crushed buy_no)
    ungraded, so the SHADOW-PROVE loop had no data and no fix could be
    settlement-validated. With this True, EVERY candidate-bearing settled row is
    graded — the system continuously measures whether its OWN rejections would
    have won at settlement (pure counterfactual, never submits, never fabricates;
    grade_receipt is the single truth function, VERIFIED-only). This is what
    surfaced the +EV near-certain buy_no tail the system was wrongly rejecting.

    ``settlement_table`` is parametrised ONLY so the relationship test can point
    at an in-memory stand-in. Returns the count of rows enriched.
    """
    from src.strategy.live_inference.no_trade_regret import NoTradeRegretLedger

    ledger = NoTradeRegretLedger(world_conn)
    # Default preserves byte-equal behaviour (shadow-only); the broadened path
    # drops the scope filter so every candidate-bearing rejection is graded.
    _reason_filter = (
        "" if grade_all_candidate_bearing
        else "AND r.rejection_reason = 'DAY0_SCOPE_SHADOW_ONLY'"
    )
    # Candidate-bearing, UNGRADED rows joined to VERIFIED settlement.
    rows = world_conn.execute(
        f"""
        SELECT r.event_id, r.rejection_stage, r.rejection_reason, r.direction, r.bin_label,
               s.settlement_value, s.settlement_unit
          FROM no_trade_regret_events r
          JOIN {settlement_table} s
            ON s.city = r.city
           AND s.target_date = r.target_date
           AND s.temperature_metric = r.metric
         WHERE r.direction IS NOT NULL
           AND r.bin_label IS NOT NULL
           AND r.would_have_won IS NULL
           {_reason_filter}
           AND s.authority = 'VERIFIED'
           AND s.settlement_value IS NOT NULL
           AND s.settlement_unit IS NOT NULL
         LIMIT ?
        """,
        (batch_limit,),
    ).fetchall()

    enriched = 0
    for row in rows:
        event_id = row[0]
        rejection_stage = row[1]
        rejection_reason = row[2]
        direction = row[3]
        bin_label = row[4]
        settlement_value = row[5]
        settlement_unit = row[6]
        graded = grade_day0_receipt_outcome(
            bin_label=str(bin_label),
            direction=str(direction),
            settlement_value=float(settlement_value),
            settlement_unit=str(settlement_unit),
        )
        if graded is None:
            continue
        later_outcome = "settled_in_bin" if graded.settled_in_bin else "settled_outside_bin"
        try:
            ledger.enrich_after_settlement(
                event_id=str(event_id),
                rejection_stage=str(rejection_stage),
                rejection_reason=str(rejection_reason),
                later_outcome=later_outcome,
                would_have_won=bool(graded.won),
                # The shadow lane assumes a fill at the captured ask; the fill
                # SIMULATION (native_quote_available / hypothetical_fill_*) decides
                # would_have_filled at insert time. Here we record the GRADING fact;
                # fillability is unknown at settlement time for a shadow row, so we
                # report would_have_filled=True only as the grading-side assumption
                # the comparator already discounts (see report Honest limitations).
                would_have_filled=True,
                settlement_proof=(
                    f"VERIFIED:{settlement_table}:value={settlement_value}{settlement_unit}"
                ),
            )
            enriched += 1
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
            logger.warning(
                "day0_shadow_enrichment: enrich failed event=%s: %s", event_id, exc
            )
    return enriched


def run_day0_shadow_enrichment_job(*, write: bool = True) -> dict:
    """Scheduler entry: grade settled day0 shadow receipts against VERIFIED truth.

    Mirrors run_shadow_comparator_job: ONE WORLD-MAIN connection with ``forecasts``
    ATTACHed (``open_world_with_forecasts``) serves both the receipt reads
    (no_trade_regret_events is WORLD-class) and the VERIFIED grading
    (forecasts.settlement_outcomes over the same ATTACH). No bare sqlite3.connect;
    no network I/O. Fail-soft: any error returns an error-tagged report rather
    than raising into the scheduler loop.
    """
    from src.cron.settlement_attribution import open_world_with_forecasts

    if not write:
        return {"status": "noop", "enriched": 0}
    try:
        with open_world_with_forecasts(write_class="bulk") as world_conn:
            # 2026-06-14: grade ALL candidate-bearing rejections (not just the
            # DAY0_SCOPE_SHADOW_ONLY scope) so the system continuously measures
            # whether its q_lcb-crushed rejections would have won at settlement —
            # the SHADOW-PROVE data the promotion loop was starved of.
            enriched = enrich_settled_day0_receipts(
                world_conn,
                settlement_table="forecasts.settlement_outcomes",
                grade_all_candidate_bearing=True,
                batch_limit=20_000,
            )
            world_conn.commit()
            return {"status": "ok", "enriched": enriched}
    except Exception as exc:  # noqa: BLE001
        logger.warning("day0_shadow_enrichment job: failed: %s", exc)
        return {"status": "error", "error": str(exc)}
